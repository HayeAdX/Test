#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Aligne les paramètres de fonctions C/C++ dans des fichiers .cpp/.h/.hpp.

Fonctionnalités :
- lit un fichier ou un dossier,
- repère les déclarations/définitions de fonctions “classiques”,
- met les paramètres sur plusieurs lignes,
- aligne les types,
- aligne les noms,
- et met au moins une tabulation réelle entre type et nom.

Utilisation :
    python align_cpp_params.py monfichier.cpp
    python align_cpp_params.py monfichier.cpp --write
    python align_cpp_params.py src include --ext .cpp .h .hpp --write

Remarque :
Le script évite volontairement certains cas trop exotiques pour limiter le
risque de casser du code (pointeurs de fonction, syntaxes atypiques, etc.).
Lance-le de préférence sur un dépôt Git propre.
"""

import argparse
import pathlib
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


ALLOWED_EXTS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}


def find_matching_paren(text: str, start: int) -> Optional[int]:
    """Trouve la parenthèse fermante correspondant à text[start] == '('."""
    assert text[start] == "("
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
    """Saute les espaces et commentaires à partir de i."""
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


def next_token(text: str, i: int) -> str:
    """Retourne le prochain token simple après la position i."""
    i = skip_ws_comments(text, i)
    if i >= len(text):
        return ""

    if text.startswith("->", i):
        return "->"
    if text.startswith("[[", i):
        return "[["
    if text[i] in "{};:=":
        return text[i]

    m = re.match(r"[A-Za-z_]\w*", text[i:])
    return m.group(0) if m else text[i]


def likely_function_signature(text: str, open_idx: int, close_idx: int) -> bool:
    """Heuristique pour décider si (...) ressemble à une signature de fonction."""
    line_start = text.rfind("\n", 0, open_idx) + 1
    prefix = text[line_start:open_idx].rstrip()

    if not prefix:
        return False

    if re.search(r"\b(?:if|for|while|switch|catch|return|sizeof|alignof|decltype|static_assert)\s*$", prefix):
        return False

    if prefix.endswith("]"):  # lambda
        return False

    if re.search(r"(?:\.|->)\s*[A-Za-z_~]\w*\s*$", prefix):
        return False

    token = next_token(text, close_idx + 1)
    return token in {"{", ";", ":", "=", "const", "noexcept", "override", "final", "requires", "->", "[["}


def split_top_level_commas(s: str) -> List[str]:
    """Découpe une liste d'arguments sur les virgules de niveau supérieur."""
    parts = []
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
                parts.append(s[start:i])
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

    parts.append(s[start:])
    return parts


def split_top_level_default(s: str) -> Tuple[str, str]:
    """Sépare 'type nom' et '= valeur_par_defaut' au niveau supérieur."""
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
                return s[:i], s[i:]

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

    return s, ""


def find_last_identifier_span(s: str) -> Optional[Tuple[int, int]]:
    """Trouve le dernier identifiant de niveau supérieur dans un argument."""
    depths = {"(": 0, "[": 0, "{": 0, "<": 0}
    state = "code"
    last = None
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
                last = (i, j)
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

    return last


@dataclass
class ParsedParam:
    original: str
    kind: str
    type_part: str = ""
    name_part: str = ""
    suffix: str = ""
    default: str = ""


def parse_param(raw: str) -> Optional[ParsedParam]:
    """Essaie de découper un argument en type / nom / suffixe / défaut."""
    s = raw.strip()

    if not s:
        return None

    if s == "...":
        return ParsedParam(original=raw, kind="ellipsis")

    if s == "void":
        return ParsedParam(original=raw, kind="other")

    if "(*" in s or "(&" in s:
        # Cas exotiques : pointeurs/références de fonction.
        return None

    base, default = split_top_level_default(s)
    base = base.rstrip()

    span = find_last_identifier_span(base)
    if span is None:
        return None

    a, b = span
    type_part = base[:a].rstrip()
    name_part = base[a:b]
    suffix = base[b:].rstrip()

    if not type_part:
        return None

    prev = base[a - 1] if a > 0 else ""
    if not (prev.isspace() or prev in "*&"):
        return None

    return ParsedParam(
        original=raw,
        kind="named",
        type_part=type_part,
        name_part=name_part,
        suffix=suffix,
        default=(" " + default.lstrip()) if default else "",
    )


def format_parameter_block(text: str, open_idx: int, close_idx: int) -> Optional[str]:
    """Reformate le bloc de paramètres situé entre open_idx et close_idx."""
    inside = text[open_idx + 1:close_idx]
    if not inside.strip():
        return None

    parts = split_top_level_commas(inside)
    if len(parts) < 2:
        return None

    parsed = []
    named_count = 0

    for part in parts:
        p = parse_param(part)
        if p is None:
            return None
        if p.kind == "named":
            named_count += 1
        parsed.append(p)

    if named_count < 2:
        return None

    line_start = text.rfind("\n", 0, open_idx) + 1
    line_end = text.find("\n", line_start)
    if line_end == -1:
        line_end = len(text)

    base_line = text[line_start:line_end]
    base_indent = re.match(r"[ \t]*", base_line).group(0)

    m = re.search(r"\n([ \t]*)\S", inside)
    param_indent = m.group(1) if m else (base_indent + "    ")

    max_type_len = max(len(p.type_part) for p in parsed if p.kind == "named")

    lines = []
    for p in parsed:
        if p.kind == "named":
            lines.append(
                "{indent}{type_part}\t{name}{suffix}{default}".format(
                    indent=param_indent,
                    type_part=p.type_part.ljust(max_type_len),
                    name=p.name_part,
                    suffix=p.suffix,
                    default=p.default,
                )
            )
        elif p.kind == "ellipsis":
            lines.append(param_indent + "...")
        else:
            lines.append(param_indent + p.original.strip())

    return "\n" + ",\n".join(lines) + "\n" + base_indent


def process_text(text: str) -> Tuple[str, int]:
    """Traite un fichier entier et retourne (texte_modifié, nombre_de_signatures)."""
    out = []
    cursor = 0
    i = 0
    changes = 0

    while i < len(text):
        if text[i] == "(":
            close_idx = find_matching_paren(text, i)
            if close_idx is not None and likely_function_signature(text, i, close_idx):
                replacement = format_parameter_block(text, i, close_idx)
                if replacement is not None:
                    out.append(text[cursor:i + 1])
                    out.append(replacement)
                    cursor = close_idx
                    i = close_idx
                    changes += 1
        i += 1

    out.append(text[cursor:])
    return "".join(out), changes


def iter_source_files(paths: List[str], extensions: List[str]) -> List[pathlib.Path]:
    """Retourne la liste triée des fichiers source correspondant aux extensions."""
    exts = set(e if e.startswith(".") else "." + e for e in extensions)
    files = []

    for p in paths:
        path = pathlib.Path(p)
        if path.is_file():
            if path.suffix.lower() in exts:
                files.append(path)
        elif path.is_dir():
            for f in path.rglob("*"):
                if f.is_file() and f.suffix.lower() in exts:
                    files.append(f)

    return sorted(set(files))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aligne les paramètres de fonctions C/C++ dans les fichiers .cpp/.h"
    )
    parser.add_argument("paths", nargs="+", help="Fichier(s) ou dossier(s) à traiter")
    parser.add_argument(
        "--ext",
        nargs="*",
        default=sorted(ALLOWED_EXTS),
        help="Extensions à traiter, ex: .cpp .h .hpp",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Écrit les changements dans les fichiers. Sinon: dry-run",
    )

    args = parser.parse_args()

    files = iter_source_files(args.paths, args.ext)
    if not files:
        print("Aucun fichier trouvé.")
        return 1

    total_files = 0
    total_changes = 0

    for path in files:
        original = path.read_text(encoding="utf-8", errors="ignore")
        updated, changes = process_text(original)

        if changes > 0 and updated != original:
            total_files += 1
            total_changes += changes

            if args.write:
                path.write_text(updated, encoding="utf-8", newline="")
                print(f"[updated] {path} ({changes} signature(s))")
            else:
                print(f"[would change] {path} ({changes} signature(s))")

    if total_files == 0:
        print("Aucun changement.")
    else:
        mode = "écrits" if args.write else "détectés"
        print(f"\n{total_files} fichier(s), {total_changes} signature(s) {mode}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
