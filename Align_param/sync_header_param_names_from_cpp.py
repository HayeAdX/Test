#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Synchronise les noms des paramètres dans les fichiers .h/.hpp à partir des
fichiers .cpp/.cc/.cxx, en utilisant les définitions du .cpp comme référence.

Objectif principal :
- si dans le .cpp on a     : foo(double XL_LimMMterm)
- et dans le .h on a       : foo(double XL_limMMterm = 0.0);
- alors le .h devient      : foo(double XL_LimMMterm = 0.0);

Le script préserve au maximum le texte original du .h :
- espaces / retours à la ligne,
- valeurs par défaut déjà présentes,
- formatage général de la déclaration.

Il ne remplace que l'identifiant du paramètre quand une correspondance fiable
est trouvée.
"""

from __future__ import annotations

import argparse
import pathlib
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

CPP_EXTS = {".c", ".cc", ".cpp", ".cxx"}
HEADER_EXTS = {".h", ".hh", ".hpp", ".hxx"}
CONTROL_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "sizeof",
    "alignof",
    "decltype",
    "static_assert",
    "new",
    "delete",
}
PURE_MODIFIERS = {
    "const",
    "volatile",
    "constexpr",
    "consteval",
    "constinit",
    "mutable",
    "register",
    "static",
    "extern",
    "inline",
    "virtual",
    "friend",
    "struct",
    "class",
    "enum",
    "typename",
    "restrict",
    "__restrict",
    "__restrict__",
    "_In_",
    "_Out_",
    "_Inout_",
}


@dataclass(frozen=True)
class ParsedParam:
    raw: str
    kind: str  # named / void / ellipsis / unsupported / unnamed
    type_key: str = ""
    name: str = ""
    name_span: Optional[Tuple[int, int]] = None  # relative to raw


@dataclass(frozen=True)
class FunctionRef:
    name: str
    key: Tuple[str, Tuple[str, ...]]
    param_names: Tuple[str, ...]
    source: str


@dataclass(frozen=True)
class Replacement:
    start: int
    end: int
    text: str


# ---------------------------------------------------------------------------
# Low level scanning helpers
# ---------------------------------------------------------------------------

def find_matching_paren(text: str, start: int) -> Optional[int]:
    if start < 0 or start >= len(text) or text[start] != "(":
        return None

    i = start + 1
    depth = 1
    state = "code"

    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if ch == '"':
                state = "string"
                i += 1
                continue
            if ch == "'":
                state = "char"
                i += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i

        elif state == "line_comment":
            if ch == "\n":
                state = "code"

        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                continue

        elif state == "string":
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                state = "code"

        elif state == "char":
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                state = "code"

        i += 1

    return None


def skip_ws_comments(text: str, i: int) -> int:
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue

        if text.startswith("//", i):
            j = text.find("\n", i + 2)
            if j == -1:
                return len(text)
            i = j + 1
            continue

        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j == -1:
                return len(text)
            i = j + 2
            continue

        break

    return i


def scan_signature_terminator(text: str, start: int) -> Optional[str]:
    """
    Retourne '{' si la signature mène à une définition,
    ';' si elle mène à une déclaration,
    None sinon.
    """
    i = start
    paren = 0
    bracket = 0
    brace = 0
    angle = 0
    state = "code"

    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if ch == '"':
                state = "string"
                i += 1
                continue
            if ch == "'":
                state = "char"
                i += 1
                continue

            if ch == "(":
                paren += 1
            elif ch == ")" and paren > 0:
                paren -= 1
            elif ch == "[":
                bracket += 1
            elif ch == "]" and bracket > 0:
                bracket -= 1
            elif ch == "<":
                angle += 1
            elif ch == ">" and angle > 0:
                angle -= 1
            elif ch == "{" and paren == 0 and bracket == 0 and brace == 0 and angle == 0:
                return "{"
            elif ch == ";" and paren == 0 and bracket == 0 and brace == 0 and angle == 0:
                return ";"

        elif state == "line_comment":
            if ch == "\n":
                state = "code"

        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                continue

        elif state == "string":
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                state = "code"

        elif state == "char":
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                state = "code"

        i += 1

    return None


def extract_function_name(text: str, open_idx: int) -> Optional[str]:
    j = open_idx - 1
    while j >= 0 and text[j].isspace():
        j -= 1

    if j < 0:
        return None

    end = j + 1
    while j >= 0 and (text[j].isalnum() or text[j] in "_::~"):
        j -= 1
    start = j + 1
    name = text[start:end]

    if not name:
        return None

    bare = name.split("::")[-1]
    if bare in CONTROL_KEYWORDS:
        return None

    if not re.match(r"^[A-Za-z_~]\w*(?:::[A-Za-z_~]\w*)*$", name):
        return None

    return name


# ---------------------------------------------------------------------------
# Parameter parsing
# ---------------------------------------------------------------------------

def split_top_level_commas_with_spans(s: str) -> List[Tuple[str, int, int]]:
    parts: List[Tuple[str, int, int]] = []
    start = 0
    depths = {"(": 0, "[": 0, "{": 0, "<": 0}
    state = "code"
    i = 0

    while i < len(s):
        ch = s[i]
        nxt = s[i + 1] if i + 1 < len(s) else ""

        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if ch == '"':
                state = "string"
                i += 1
                continue
            if ch == "'":
                state = "char"
                i += 1
                continue

            if ch == "(":
                depths["("] += 1
            elif ch == ")" and depths["("] > 0:
                depths["("] -= 1
            elif ch == "[":
                depths["["] += 1
            elif ch == "]" and depths["["] > 0:
                depths["["] -= 1
            elif ch == "{":
                depths["{"] += 1
            elif ch == "}" and depths["{"] > 0:
                depths["{"] -= 1
            elif ch == "<":
                depths["<"] += 1
            elif ch == ">" and depths["<"] > 0:
                depths["<"] -= 1
            elif ch == "," and all(v == 0 for v in depths.values()):
                parts.append((s[start:i], start, i))
                start = i + 1

        elif state == "line_comment":
            if ch == "\n":
                state = "code"

        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                continue

        elif state == "string":
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                state = "code"

        elif state == "char":
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                state = "code"

        i += 1

    parts.append((s[start:], start, len(s)))
    return parts


def split_top_level_default_index(s: str) -> Optional[int]:
    depths = {"(": 0, "[": 0, "{": 0, "<": 0}
    state = "code"
    i = 0

    while i < len(s):
        ch = s[i]
        nxt = s[i + 1] if i + 1 < len(s) else ""
        prev = s[i - 1] if i > 0 else ""

        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if ch == '"':
                state = "string"
                i += 1
                continue
            if ch == "'":
                state = "char"
                i += 1
                continue

            if ch == "(":
                depths["("] += 1
            elif ch == ")" and depths["("] > 0:
                depths["("] -= 1
            elif ch == "[":
                depths["["] += 1
            elif ch == "]" and depths["["] > 0:
                depths["["] -= 1
            elif ch == "{":
                depths["{"] += 1
            elif ch == "}" and depths["{"] > 0:
                depths["{"] -= 1
            elif ch == "<":
                depths["<"] += 1
            elif ch == ">" and depths["<"] > 0:
                depths["<"] -= 1
            elif ch == "=" and all(v == 0 for v in depths.values()) and prev not in "<>!=" and nxt != "=":
                return i

        elif state == "line_comment":
            if ch == "\n":
                state = "code"

        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                continue

        elif state == "string":
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                state = "code"

        elif state == "char":
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                state = "code"

        i += 1

    return None


def top_level_identifiers(s: str) -> List[Tuple[str, int, int]]:
    ids: List[Tuple[str, int, int]] = []
    depths = {"(": 0, "[": 0, "{": 0, "<": 0}
    state = "code"
    i = 0

    while i < len(s):
        ch = s[i]
        nxt = s[i + 1] if i + 1 < len(s) else ""

        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if ch == '"':
                state = "string"
                i += 1
                continue
            if ch == "'":
                state = "char"
                i += 1
                continue

            if ch == "(":
                depths["("] += 1
            elif ch == ")" and depths["("] > 0:
                depths["("] -= 1
            elif ch == "[":
                depths["["] += 1
            elif ch == "]" and depths["["] > 0:
                depths["["] -= 1
            elif ch == "{":
                depths["{"] += 1
            elif ch == "}" and depths["{"] > 0:
                depths["{"] -= 1
            elif ch == "<":
                depths["<"] += 1
            elif ch == ">" and depths["<"] > 0:
                depths["<"] -= 1
            elif all(v == 0 for v in depths.values()) and (ch.isalpha() or ch == "_"):
                j = i + 1
                while j < len(s) and (s[j].isalnum() or s[j] == "_"):
                    j += 1
                ids.append((s[i:j], i, j))
                i = j
                continue

        elif state == "line_comment":
            if ch == "\n":
                state = "code"

        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                continue

        elif state == "string":
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                state = "code"

        elif state == "char":
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                state = "code"

        i += 1

    return ids


def normalize_type_key(s: str) -> str:
    return re.sub(r"\s+", "", s)


def parse_param(raw: str) -> ParsedParam:
    stripped = raw.strip()
    if not stripped:
        return ParsedParam(raw=raw, kind="unsupported")

    if stripped == "...":
        return ParsedParam(raw=raw, kind="ellipsis", type_key="...")

    if stripped == "void":
        return ParsedParam(raw=raw, kind="void", type_key="void")

    if "(*" in stripped or "(&" in stripped:
        return ParsedParam(raw=raw, kind="unsupported")

    default_idx = split_top_level_default_index(raw)
    base = raw if default_idx is None else raw[:default_idx]

    ids = top_level_identifiers(base)
    if not ids:
        return ParsedParam(raw=raw, kind="unsupported")

    _, a, b = ids[-1]
    prev = base[a - 1] if a > 0 else ""
    before = base[:a]
    after = base[b:]
    after_non_ws = after.lstrip()

    # Le nom doit être séparé du type par un espace ou * / &.
    if not prev or (not prev.isspace() and prev not in "*&"):
        return ParsedParam(raw=raw, kind="unsupported")

    # Après le nom, on tolère seulement fin de chaîne, espaces ou suffixe de tableau.
    if after_non_ws and not after_non_ws.startswith("["):
        return ParsedParam(raw=raw, kind="unsupported")

    ids_before = [token for token, _, _ in ids[:-1]]
    before_stripped = before.rstrip()
    before_has_pointer_like = bool(before_stripped) and before_stripped[-1] in "*&>)]"

    # Cas typique d'un paramètre sans nom : "const MyType" ou "std::string"
    # On évite de confondre le dernier identifiant du type avec un nom.
    if not ids_before and not before_has_pointer_like:
        return ParsedParam(raw=raw, kind="unnamed", type_key=normalize_type_key(base))

    if ids_before and all(tok in PURE_MODIFIERS for tok in ids_before) and not before_has_pointer_like:
        return ParsedParam(raw=raw, kind="unnamed", type_key=normalize_type_key(base))

    type_text = base[:a] + base[b:]
    return ParsedParam(
        raw=raw,
        kind="named",
        type_key=normalize_type_key(type_text),
        name=base[a:b],
        name_span=(a, b),
    )


def parse_parameter_list(inside: str) -> Optional[List[ParsedParam]]:
    parts = split_top_level_commas_with_spans(inside)
    parsed: List[ParsedParam] = []

    # Cas foo(void)
    if len(parts) == 1 and parts[0][0].strip() == "void":
        return []

    for raw, _start, _end in parts:
        p = parse_param(raw)
        if p.kind == "unsupported":
            return None
        parsed.append(p)

    return parsed


# ---------------------------------------------------------------------------
# Function extraction
# ---------------------------------------------------------------------------

def function_key(name: str, params: Sequence[ParsedParam]) -> Tuple[str, Tuple[str, ...]]:
    return name, tuple(p.type_key for p in params if p.kind != "void")


def collect_cpp_references(path: pathlib.Path) -> List[FunctionRef]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    refs: List[FunctionRef] = []
    i = 0

    while i < len(text):
        if text[i] != "(":
            i += 1
            continue

        close_idx = find_matching_paren(text, i)
        if close_idx is None:
            i += 1
            continue

        name = extract_function_name(text, i)
        if not name:
            i = close_idx + 1
            continue

        terminator = scan_signature_terminator(text, close_idx + 1)
        if terminator != "{":
            i = close_idx + 1
            continue

        inside = text[i + 1:close_idx]
        params = parse_parameter_list(inside)
        if params is None:
            i = close_idx + 1
            continue

        # On ne garde que les fonctions où tous les paramètres ont bien un nom.
        if any(p.kind not in {"named", "ellipsis"} for p in params):
            i = close_idx + 1
            continue

        if any(p.kind == "ellipsis" for p in params):
            i = close_idx + 1
            continue

        key = function_key(name, params)
        refs.append(
            FunctionRef(
                name=name,
                key=key,
                param_names=tuple(p.name for p in params),
                source=str(path),
            )
        )
        i = close_idx + 1

    return refs


def compute_header_replacements(path: pathlib.Path, ref_map: Dict[Tuple[str, Tuple[str, ...]], FunctionRef]) -> Tuple[List[Replacement], int]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    replacements: List[Replacement] = []
    i = 0
    matched_functions = 0

    while i < len(text):
        if text[i] != "(":
            i += 1
            continue

        close_idx = find_matching_paren(text, i)
        if close_idx is None:
            i += 1
            continue

        name = extract_function_name(text, i)
        if not name:
            i = close_idx + 1
            continue

        terminator = scan_signature_terminator(text, close_idx + 1)
        if terminator != ";":
            i = close_idx + 1
            continue

        inside = text[i + 1:close_idx]
        parts = split_top_level_commas_with_spans(inside)
        if len(parts) == 1 and parts[0][0].strip() == "void":
            params: List[ParsedParam] = []
        else:
            params = []
            for raw, start, end in parts:
                parsed = parse_param(raw)
                if parsed.kind == "unsupported":
                    params = []
                    break
                if parsed.kind == "ellipsis":
                    params = []
                    break
                # On n'essaye pas d'insérer un nom si le .h n'en a pas déjà un.
                # Le but ici est de renommer de façon sûre et non de reformater.
                params.append(parsed)
            else:
                pass

            if not parts:
                params = []

            # Si un paramètre est unsupported / ellipsis, on saute.
            if parts and len(params) != len(parts):
                i = close_idx + 1
                continue

        key = function_key(name, params)
        ref = ref_map.get(key)
        if ref is None:
            i = close_idx + 1
            continue

        if len(ref.param_names) != len(params):
            i = close_idx + 1
            continue

        matched_functions += 1

        for ref_name, (raw, part_start, _part_end), header_param in zip(ref.param_names, parts, params):
            if header_param.kind != "named":
                continue
            if header_param.name == ref_name:
                continue
            if header_param.name_span is None:
                continue

            name_start_rel, name_end_rel = header_param.name_span
            abs_start = i + 1 + part_start + name_start_rel
            abs_end = i + 1 + part_start + name_end_rel
            replacements.append(Replacement(start=abs_start, end=abs_end, text=ref_name))

        i = close_idx + 1

    return replacements, matched_functions


# ---------------------------------------------------------------------------
# File helpers and main logic
# ---------------------------------------------------------------------------

def iter_files(paths: Iterable[str], exts: Iterable[str]) -> List[pathlib.Path]:
    wanted = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in exts}
    out: List[pathlib.Path] = []

    for raw in paths:
        path = pathlib.Path(raw)
        if path.is_file():
            if path.suffix.lower() in wanted:
                out.append(path)
            continue
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and child.suffix.lower() in wanted:
                    out.append(child)

    return sorted(set(out))


def apply_replacements(text: str, replacements: Sequence[Replacement]) -> str:
    if not replacements:
        return text

    ordered = sorted(replacements, key=lambda r: r.start, reverse=True)
    result = text
    last_start = None

    for rep in ordered:
        if last_start is not None and rep.end > last_start:
            continue
        result = result[:rep.start] + rep.text + result[rep.end:]
        last_start = rep.start

    return result


def build_reference_map(cpp_files: Sequence[pathlib.Path]) -> Tuple[Dict[Tuple[str, Tuple[str, ...]], FunctionRef], List[str]]:
    ref_map: Dict[Tuple[str, Tuple[str, ...]], FunctionRef] = {}
    warnings: List[str] = []

    for path in cpp_files:
        for ref in collect_cpp_references(path):
            existing = ref_map.get(ref.key)
            if existing is None:
                ref_map[ref.key] = ref
                continue

            if existing.param_names != ref.param_names:
                warnings.append(
                    "Conflit pour {}{} entre {} et {} ; entrée ignorée.".format(
                        ref.name,
                        ref.key[1],
                        existing.source,
                        ref.source,
                    )
                )

    return ref_map, warnings


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Renomme les paramètres dans les .h/.hpp pour qu'ils portent les mêmes "
            "noms que dans les définitions .cpp/.cc/.cxx correspondantes."
        )
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Fichier(s) ou dossier(s) à analyser",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Écrit les changements dans les fichiers .h/.hpp ; sinon dry-run.",
    )
    parser.add_argument(
        "--cpp-ext",
        nargs="*",
        default=sorted(CPP_EXTS),
        help="Extensions de fichiers source à utiliser comme référence (.cpp/.cc/...)",
    )
    parser.add_argument(
        "--header-ext",
        nargs="*",
        default=sorted(HEADER_EXTS),
        help="Extensions de fichiers header à modifier (.h/.hpp/...)",
    )

    args = parser.parse_args()

    cpp_files = iter_files(args.paths, args.cpp_ext)
    header_files = iter_files(args.paths, args.header_ext)

    if not cpp_files:
        print("Aucun fichier .cpp/.cc/.cxx trouvé.")
        return 1
    if not header_files:
        print("Aucun fichier .h/.hpp/.hh/.hxx trouvé.")
        return 1

    ref_map, warnings = build_reference_map(cpp_files)
    if not ref_map:
        print("Aucune définition de fonction exploitable trouvée dans les fichiers source.")
        return 1

    total_files_changed = 0
    total_name_replacements = 0
    total_functions_matched = 0

    for header_path in header_files:
        replacements, matched_functions = compute_header_replacements(header_path, ref_map)
        total_functions_matched += matched_functions
        if not replacements:
            continue

        original = header_path.read_text(encoding="utf-8", errors="ignore")
        updated = apply_replacements(original, replacements)
        if updated == original:
            continue

        total_files_changed += 1
        total_name_replacements += len(replacements)

        if args.write:
            header_path.write_text(updated, encoding="utf-8", newline="")
            print(f"[updated] {header_path} ({len(replacements)} renommage(s))")
        else:
            print(f"[would change] {header_path} ({len(replacements)} renommage(s))")

    if warnings:
        print("\nAvertissements :")
        for warning in warnings:
            print(f"- {warning}")

    if total_files_changed == 0:
        print("Aucun changement.")
    else:
        mode = "écrits" if args.write else "détectés"
        print(
            "\n{} fichier(s) header modifié(s), {} renommage(s) {}, {} fonction(s) header appariée(s).".format(
                total_files_changed,
                total_name_replacements,
                mode,
                total_functions_matched,
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
