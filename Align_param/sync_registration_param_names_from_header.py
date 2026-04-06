#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Synchronise les noms de paramètres d'un faux .h de registration XLL
(depuis un vrai .h C/C++).

Le script :
- retrouve les paramètres dans le vrai .h,
- enlève le préfixe XL_ / xl_,
- met à jour le 4e wide-string du faux .h (liste des paramètres),
- met à jour aussi le 10e wide-string si présent, en essayant de remplacer
  le premier mot de chaque segment séparé par des virgules.

Usage :
    python sync_registration_param_names_from_header.py fake_register.h real_api.h
    python sync_registration_param_names_from_header.py fake_register.h real_api.h --write
"""

from __future__ import annotations

import argparse
import pathlib
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Outils parsing C/C++
# ---------------------------------------------------------------------------


def find_matching_paren(text: str, start: int) -> Optional[int]:
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
    i = skip_ws_comments(text, i)
    if i >= len(text):
        return ""

    if text.startswith("->", i):
        return "->"
    if text.startswith("[[", i):
        return "[["
    if text[i] in "{};:=" or text[i] == ",":
        return text[i]

    m = re.match(r"[A-Za-z_~]\w*", text[i:])
    return m.group(0) if m else text[i]


def likely_function_signature(text: str, open_idx: int, close_idx: int) -> bool:
    line_start = text.rfind("\n", 0, open_idx) + 1
    prefix = text[line_start:open_idx].rstrip()

    if not prefix:
        return False

    if re.search(r"\b(?:if|for|while|switch|catch|return|sizeof|alignof|decltype|static_assert)\s*$", prefix):
        return False

    if prefix.endswith("]"):
        return False

    if re.search(r"(?:\.|->)\s*[A-Za-z_~]\w*\s*$", prefix):
        return False

    token = next_token(text, close_idx + 1)
    return token in {"{", ";", ":", "=", "const", "noexcept", "override", "final", "requires", "->", "[["}


def split_top_level_commas(s: str) -> List[str]:
    parts: List[str] = []
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
    depths = {"(": 0, "[": 0, "{": 0, "<": 0}
    state = "code"
    last: Optional[Tuple[int, int]] = None
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


def extract_function_name_before_paren(text: str, open_idx: int) -> Optional[str]:
    i = open_idx - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    if i < 0:
        return None

    end = i + 1
    while i >= 0 and (text[i].isalnum() or text[i] in "_~"):
        i -= 1

    name = text[i + 1:end]
    if not re.fullmatch(r"[A-Za-z_~]\w*", name):
        return None
    return name


@dataclass
class ParsedParam:
    kind: str
    name_part: str = ""


def parse_param_name(raw: str) -> Optional[ParsedParam]:
    s = raw.strip()
    if not s:
        return None
    if s == "...":
        return ParsedParam(kind="ellipsis")
    if s == "void":
        return ParsedParam(kind="other")
    if "(*" in s or "(&" in s:
        return None

    base, _default = split_top_level_default(s)
    base = base.rstrip()
    span = find_last_identifier_span(base)
    if span is None:
        return None

    a, b = span
    type_part = base[:a].rstrip()
    name_part = base[a:b]
    prev = base[a - 1] if a > 0 else ""

    if not type_part:
        return None
    if not (prev.isspace() or prev in "*&"):
        return None

    return ParsedParam(kind="named", name_part=name_part)


def strip_xl_prefix(name: str) -> str:
    if name.lower().startswith("xl_"):
        return name[3:]
    return name


def parse_real_header_functions(text: str) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = {}
    i = 0

    while i < len(text):
        if text[i] == "(":
            close_idx = find_matching_paren(text, i)
            if close_idx is not None and likely_function_signature(text, i, close_idx):
                func_name = extract_function_name_before_paren(text, i)
                if func_name:
                    inside = text[i + 1:close_idx]
                    parts = split_top_level_commas(inside)
                    names: List[str] = []
                    ok = True
                    for part in parts:
                        parsed = parse_param_name(part)
                        if parsed is None:
                            ok = False
                            break
                        if parsed.kind == "named":
                            names.append(strip_xl_prefix(parsed.name_part))
                    if ok and names:
                        mapping[func_name] = names
                i = close_idx
        i += 1

    return mapping


# ---------------------------------------------------------------------------
# Outils parsing faux .h de registration
# ---------------------------------------------------------------------------


STRING_RE = re.compile(r'L"((?:\\.|[^"\\])*)"')


@dataclass
class StringLiteralInfo:
    full_start: int
    full_end: int
    content_start: int
    content_end: int
    content: str


@dataclass
class BlockInfo:
    start: int
    end: int
    strings: List[StringLiteralInfo]


def find_matching_brace(text: str, start: int) -> Optional[int]:
    assert text[start] == "{"
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

            if ch == "{":
                depth += 1
            elif ch == "}":
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


def extract_blocks_with_strings(text: str) -> List[BlockInfo]:
    blocks: List[BlockInfo] = []
    i = 0
    while i < len(text):
        if text[i] == "{":
            close_idx = find_matching_brace(text, i)
            if close_idx is not None:
                block_text = text[i:close_idx + 1]
                strings: List[StringLiteralInfo] = []
                for m in STRING_RE.finditer(block_text):
                    strings.append(
                        StringLiteralInfo(
                            full_start=i + m.start(),
                            full_end=i + m.end(),
                            content_start=i + m.start(1),
                            content_end=i + m.end(1),
                            content=m.group(1),
                        )
                    )
                if strings:
                    blocks.append(BlockInfo(start=i, end=close_idx + 1, strings=strings))
                i = close_idx
        i += 1
    return blocks


def escape_c_wide_string_content(s: str) -> str:
    return s.replace('\\', r'\\').replace('"', r'\"')


def preserve_trailing_space(old: str, new: str) -> str:
    if old.endswith(" ") and not new.endswith(" "):
        return new + " "
    return new


def rewrite_argument_help(old_text: str, new_names: Sequence[str]) -> str:
    stripped = old_text.strip()
    if not stripped:
        return old_text

    parts = old_text.split(",")
    if len(parts) != len(new_names):
        if len(new_names) == 1:
            m = re.match(r"^(\s*)([A-Za-z_]\w*)(.*)$", old_text, flags=re.DOTALL)
            if m:
                return f"{m.group(1)}{new_names[0]}{m.group(3)}"
        return old_text

    rewritten: List[str] = []
    for part, new_name in zip(parts, new_names):
        m = re.match(r"^(\s*)([A-Za-z_]\w*)(.*)$", part, flags=re.DOTALL)
        if m:
            rewritten.append(f"{m.group(1)}{new_name}{m.group(3)}")
        else:
            rewritten.append(part)
    return ",".join(rewritten)


# ---------------------------------------------------------------------------
# Traitement principal
# ---------------------------------------------------------------------------


def update_fake_registration_text(
    fake_text: str,
    real_mapping: Dict[str, List[str]],
    param_field_index: int = 4,
    help_field_index: int = 10,
) -> Tuple[str, int, List[str]]:
    """
    Met à jour le faux fichier de registration.

    param_field_index et help_field_index sont 1-based à l'intérieur d'un bloc.
    Par défaut :
    - 4e string = argument_text
    - 10e string = argument_help
    """
    blocks = extract_blocks_with_strings(fake_text)
    replacements: List[Tuple[int, int, str]] = []
    updated_functions: List[str] = []

    for block in blocks:
        if not block.strings:
            continue

        func_name = block.strings[0].content
        if func_name not in real_mapping:
            continue

        new_names = real_mapping[func_name]
        if not new_names:
            continue

        changed = False

        # 4e champ : on remplace la liste complète des paramètres.
        if len(block.strings) >= param_field_index:
            s = block.strings[param_field_index - 1]
            new_text = ", ".join(new_names)
            new_text = preserve_trailing_space(s.content, new_text)
            if s.content != new_text:
                replacements.append((s.content_start, s.content_end, escape_c_wide_string_content(new_text)))
                changed = True

        # 10e champ : on remplace le premier mot de chaque segment séparé par virgules.
        if len(block.strings) >= help_field_index:
            s = block.strings[help_field_index - 1]
            new_text = rewrite_argument_help(s.content, new_names)
            new_text = preserve_trailing_space(s.content, new_text)
            if s.content != new_text:
                replacements.append((s.content_start, s.content_end, escape_c_wide_string_content(new_text)))
                changed = True

        if changed:
            updated_functions.append(func_name)

    if not replacements:
        return fake_text, 0, []

    replacements.sort(key=lambda x: x[0], reverse=True)
    out = fake_text
    for start, end, repl in replacements:
        out = out[:start] + repl + out[end:]

    return out, len(updated_functions), updated_functions


def guess_output_path(input_path: pathlib.Path) -> pathlib.Path:
    return input_path.with_name(input_path.stem + ".synced" + input_path.suffix)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronise les noms de paramètres d'un faux .h de registration depuis un vrai .h"
    )
    parser.add_argument("fake_header", help="Faux .h / fichier de registration à modifier")
    parser.add_argument("real_header", help="Vrai .h C/C++ servant de source de vérité")
    parser.add_argument("--write", action="store_true", help="Écrit directement dans fake_header")
    parser.add_argument(
        "--output",
        help="Chemin de sortie si --write n'est pas utilisé (défaut: <fichier>.synced.h)",
    )
    parser.add_argument(
        "--param-field-index",
        type=int,
        default=4,
        help="Index 1-based du champ contenant la liste des noms de paramètres (défaut: 4)",
    )
    parser.add_argument(
        "--help-field-index",
        type=int,
        default=10,
        help="Index 1-based du champ argument_help à synchroniser aussi (défaut: 10)",
    )

    args = parser.parse_args()

    fake_path = pathlib.Path(args.fake_header)
    real_path = pathlib.Path(args.real_header)

    if not fake_path.is_file():
        print(f"Fichier introuvable : {fake_path}")
        return 1
    if not real_path.is_file():
        print(f"Fichier introuvable : {real_path}")
        return 1
    if args.param_field_index <= 0 or args.help_field_index <= 0:
        print("--param-field-index et --help-field-index doivent être > 0")
        return 1

    fake_text = fake_path.read_text(encoding="utf-8", errors="ignore")
    real_text = real_path.read_text(encoding="utf-8", errors="ignore")

    real_mapping = parse_real_header_functions(real_text)
    if not real_mapping:
        print("Aucune signature de fonction exploitable trouvée dans le vrai .h")
        return 1

    updated_text, updated_count, updated_functions = update_fake_registration_text(
        fake_text,
        real_mapping,
        param_field_index=args.param_field_index,
        help_field_index=args.help_field_index,
    )

    if updated_count == 0:
        print("Aucun changement.")
        return 0

    if args.write:
        out_path = fake_path
    else:
        out_path = pathlib.Path(args.output) if args.output else guess_output_path(fake_path)

    out_path.write_text(updated_text, encoding="utf-8", newline="")

    print(f"Fichier écrit : {out_path}")
    print(f"Fonctions mises à jour : {updated_count}")
    for name in updated_functions[:20]:
        print(f"- {name}")
    if len(updated_functions) > 20:
        print(f"... et {len(updated_functions) - 20} autre(s)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
