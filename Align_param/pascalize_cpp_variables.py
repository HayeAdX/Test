#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Renomme en PascalCase les noms de variables dans un .cpp à partir d'un
répertoire de mots produit par build_header_word_repository.py.

Ce script travaille en "best effort" sur :
- les paramètres des définitions de fonctions,
- les variables locales simples dans les corps de fonctions,
- les variables déclarées dans les for/range-for simples.

Règles spéciales :
- XL_curvedata  -> XL_CurveData
- xl_curvedata  -> XL_CurveData
- xl_curve_data -> XL_CurveData
- si le nom commence déjà par XL_, le préfixe XL_ est conservé tel quel.

Exemples :
    python pascalize_cpp_variables.py monfichier.cpp --write
    python pascalize_cpp_variables.py src/ --repo param_word_repository.json --write
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple


CPP_EXTS = {".c", ".cc", ".cpp", ".cxx"}
CPP_KEYWORDS = {
    "alignas", "alignof", "and", "and_eq", "asm", "auto", "bitand", "bitor",
    "bool", "break", "case", "catch", "char", "class", "compl", "concept",
    "const", "consteval", "constexpr", "constinit", "const_cast", "continue",
    "co_await", "co_return", "co_yield", "decltype", "default", "delete", "do",
    "double", "dynamic_cast", "else", "enum", "explicit", "export", "extern",
    "false", "float", "for", "friend", "goto", "if", "inline", "int", "long",
    "mutable", "namespace", "new", "noexcept", "not", "not_eq", "nullptr",
    "operator", "or", "or_eq", "private", "protected", "public", "register",
    "reinterpret_cast", "requires", "return", "short", "signed", "sizeof",
    "static", "static_assert", "static_cast", "struct", "switch", "template",
    "this", "thread_local", "throw", "true", "try", "typedef", "typeid",
    "typename", "union", "unsigned", "using", "virtual", "void", "volatile",
    "wchar_t", "while", "xor", "xor_eq",
}
NON_DECL_PREFIXES = {
    "return", "if", "while", "switch", "delete", "new", "goto", "throw",
    "break", "continue", "case", "else", "do", "using", "typedef",
}


@dataclass
class ParsedParam:
    original: str
    kind: str
    type_part: str = ""
    name_part: str = ""
    suffix: str = ""
    default: str = ""


@dataclass
class FunctionRegion:
    open_paren: int
    close_paren: int
    open_brace: int
    close_brace: int


# ---------- Analyse C/C++ de base ----------

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
    if text[i] in "{};:=":
        return text[i]

    m = re.match(r"[A-Za-z_]\w*", text[i:])
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


def find_function_body_start(text: str, close_idx: int) -> Optional[int]:
    i = close_idx + 1
    state = "code"
    paren = bracket = brace = angle = 0

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
            elif ch == "{":
                if paren == 0 and bracket == 0 and angle == 0:
                    return i
                brace += 1
            elif ch == "}" and brace > 0:
                brace -= 1
            elif ch == ";":
                if paren == 0 and bracket == 0 and angle == 0:
                    return None

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


def split_top_level_commas(s: str) -> List[str]:
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


def parse_param(raw: str) -> Optional[ParsedParam]:
    s = raw.strip()

    if not s:
        return None
    if s == "...":
        return ParsedParam(original=raw, kind="ellipsis")
    if s == "void":
        return ParsedParam(original=raw, kind="other")
    if "(*" in s or "(&" in s:
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


def find_functions(text: str) -> List[FunctionRegion]:
    out: List[FunctionRegion] = []
    i = 0
    while i < len(text):
        if text[i] == "(":
            close_idx = find_matching_paren(text, i)
            if close_idx is not None and likely_function_signature(text, i, close_idx):
                open_brace = find_function_body_start(text, close_idx)
                if open_brace is not None:
                    close_brace = find_matching_brace(text, open_brace)
                    if close_brace is not None:
                        out.append(FunctionRegion(i, close_idx, open_brace, close_brace))
                        i = close_brace
        i += 1
    return out


# ---------- Répertoire de mots ----------

def load_repo_words(repo_path: pathlib.Path) -> Dict[str, str]:
    data = json.loads(repo_path.read_text(encoding="utf-8"))
    words = data.get("words", {})
    if not isinstance(words, dict):
        raise ValueError(f"Répertoire invalide: {repo_path}")
    return {str(k).lower(): str(v) for k, v in words.items()}


def split_camel_and_snake(identifier: str) -> List[str]:
    parts: List[str] = []
    for chunk in re.split(r"[^A-Za-z0-9]+", identifier):
        if not chunk:
            continue
        parts.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+", chunk))
    return parts


def default_canonical(word: str) -> str:
    if word.isdigit():
        return word
    return word[:1].upper() + word[1:].lower()


def segment_lower_chunk(chunk: str, repo_words: Dict[str, str]) -> List[str]:
    s = chunk.lower()
    memo: Dict[int, Optional[List[str]]] = {}
    keys_by_len = sorted(repo_words.keys(), key=len, reverse=True)

    def solve(i: int) -> Optional[List[str]]:
        if i == len(s):
            return []
        if i in memo:
            return memo[i]

        best: Optional[List[str]] = None
        for key in keys_by_len:
            if not s.startswith(key, i):
                continue
            tail = solve(i + len(key))
            if tail is None:
                continue
            candidate = [repo_words[key]] + tail
            if best is None or len(candidate) < len(best):
                best = candidate
        memo[i] = best
        return best

    result = solve(0)
    if result:
        return result
    return [default_canonical(chunk)]


def identifier_to_words(identifier: str, repo_words: Dict[str, str]) -> List[str]:
    words: List[str] = []
    for raw_part in re.split(r"[_\W]+", identifier):
        if not raw_part:
            continue

        split_parts = split_camel_and_snake(raw_part)
        if split_parts:
            # Si split_parts contient un seul bloc minuscule, on tente une segmentation via le repo.
            if len(split_parts) == 1 and split_parts[0].islower() and split_parts[0].lower() not in repo_words:
                words.extend(segment_lower_chunk(split_parts[0], repo_words))
            else:
                for w in split_parts:
                    words.append(repo_words.get(w.lower(), default_canonical(w)))
        else:
            words.extend(segment_lower_chunk(raw_part, repo_words))
    return [w for w in words if w]


def pascalize_identifier(name: str, repo_words: Dict[str, str]) -> str:
    if not name or name in CPP_KEYWORDS:
        return name
    if name.startswith("__"):
        return name

    prefix = ""
    rest = name

    if re.match(r"(?i)^xl_", name):
        prefix = "XL_"
        rest = name[3:]

    words = identifier_to_words(rest, repo_words)
    if not words:
        return prefix or name

    return prefix + "".join(words)


# ---------- Déclarations locales ----------

FIRST_DECL_RE = re.compile(
    r"""
    ^\s*
    (?:(?:static|const|constexpr|volatile|mutable|register|extern|typename|signed|unsigned|short|long|auto)\s+)*
    (?:[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*(?:\s*<[^;{}()]*>)?|void|bool|char|short|int|long|float|double)   # type
    (?:\s+|\s*[*&]+\s*)
    ([A-Za-z_]\w*)
    """,
    re.X,
)

COMMA_DECL_RE = re.compile(r",\s*[*&\s]*([A-Za-z_]\w*)")


def extract_declared_names_from_statement(stmt: str) -> List[str]:
    s = stmt.strip().rstrip(";").strip()
    if not s:
        return []
    if s.startswith("#"):
        return []

    first_word_match = re.match(r"([A-Za-z_]\w*)", s)
    first_word = first_word_match.group(1) if first_word_match else ""
    if first_word in NON_DECL_PREFIXES:
        return []
    if re.search(r"\b(?:if|for|while|switch|catch)\s*\(", s):
        return []
    if "operator" in s:
        return []
    if re.search(r"\bauto\s*\[", s):
        return []  # structured bindings ignorés

    m = FIRST_DECL_RE.match(s)
    if not m:
        return []

    names = [m.group(1)]
    for extra in COMMA_DECL_RE.findall(s[m.end():]):
        names.append(extra)
    return names


def split_top_level_semicolons_or_colon(s: str) -> List[Tuple[str, str]]:
    parts: List[Tuple[str, str]] = []
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
            elif all(v == 0 for v in depths.values()):
                if ch == ";":
                    parts.append((s[start:i], ";"))
                    start = i + 1
                elif ch == ":":
                    if not (i + 1 < len(s) and s[i + 1] == ":") and not (i > 0 and s[i - 1] == ":"):
                        parts.append((s[start:i], ":"))
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

    parts.append((s[start:], ""))
    return parts


def collect_statement_snippets(body_text: str) -> List[str]:
    snippets: List[str] = []
    state = "code"
    paren = 0
    bracket = 0
    brace = 0
    start = 0
    i = 0

    while i < len(body_text):
        ch = body_text[i]
        nxt = body_text[i + 1] if i + 1 < len(body_text) else ""

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
            elif ch == "{":
                if paren == 0 and bracket == 0:
                    start = i + 1
                brace += 1
            elif ch == "}" and brace > 0:
                brace -= 1
                if paren == 0 and bracket == 0:
                    start = i + 1
            elif ch == ";" and paren == 0 and bracket == 0:
                snippet = body_text[start:i + 1]
                if snippet.strip():
                    snippets.append(snippet)
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

    return snippets


def collect_for_headers(body_text: str) -> List[str]:
    headers: List[str] = []
    state = "code"
    i = 0

    while i < len(body_text):
        ch = body_text[i]
        nxt = body_text[i + 1] if i + 1 < len(body_text) else ""

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

            if (ch.isalpha() or ch == "_"):
                j = i + 1
                while j < len(body_text) and (body_text[j].isalnum() or body_text[j] == "_"):
                    j += 1
                token = body_text[i:j]
                if token == "for":
                    k = skip_ws_comments(body_text, j)
                    if k < len(body_text) and body_text[k] == "(":
                        end = find_matching_paren(body_text, k)
                        if end is not None:
                            headers.append(body_text[k + 1:end])
                            i = end
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

    return headers


def local_declared_names(body_text: str) -> List[str]:
    names: List[str] = []

    for stmt in collect_statement_snippets(body_text):
        names.extend(extract_declared_names_from_statement(stmt))

    for header in collect_for_headers(body_text):
        parts = split_top_level_semicolons_or_colon(header)
        if not parts:
            continue
        init = parts[0][0].strip()
        sep = parts[0][1]
        if not init:
            continue
        if sep in {";", ":"}:
            names.extend(extract_declared_names_from_statement(init + ";"))

    return names


# ---------- Remplacement sûr d'identifiants ----------

def collect_identifiers(text: str) -> Set[str]:
    ids: Set[str] = set()
    state = "code"
    i = 0

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
            if ch.isalpha() or ch == "_":
                j = i + 1
                while j < len(text) and (text[j].isalnum() or text[j] == "_"):
                    j += 1
                ids.add(text[i:j])
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


def previous_non_space(text: str, idx: int) -> Tuple[str, str]:
    j = idx - 1
    while j >= 0 and text[j].isspace():
        j -= 1
    if j < 0:
        return "", ""
    prev = text[j]
    prev2 = text[j - 1:j + 1] if j - 1 >= 0 else prev
    return prev, prev2


def next_non_space(text: str, idx: int) -> Tuple[str, str]:
    j = idx
    while j < len(text) and text[j].isspace():
        j += 1
    if j >= len(text):
        return "", ""
    nxt = text[j]
    nxt2 = text[j:j + 2]
    return nxt, nxt2


def should_replace_identifier(text: str, start: int, end: int) -> bool:
    prev, prev2 = previous_non_space(text, start)
    nxt, nxt2 = next_non_space(text, end)

    if prev == ".":
        return False
    if prev2 == "->":
        return False
    if prev2 == "::":
        return False
    if nxt == ":" and nxt2 != "::":
        return False
    return True


def replace_identifiers_in_region(text: str, start: int, end: int, rename_map: Dict[str, str]) -> str:
    region = text[start:end]
    out: List[str] = []
    state = "code"
    i = 0

    while i < len(region):
        ch = region[i]
        nxt = region[i + 1] if i + 1 < len(region) else ""

        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                out.append(ch)
                i += 1
            elif ch == "/" and nxt == "*":
                state = "block_comment"
                out.append(ch)
                i += 1
            elif ch == '"':
                state = "string"
                out.append(ch)
            elif ch == "'":
                state = "char"
                out.append(ch)
            elif ch.isalpha() or ch == "_":
                j = i + 1
                while j < len(region) and (region[j].isalnum() or region[j] == "_"):
                    j += 1
                token = region[i:j]
                if token in rename_map and should_replace_identifier(region, i, j):
                    out.append(rename_map[token])
                else:
                    out.append(token)
                i = j
                continue
            else:
                out.append(ch)

        elif state == "line_comment":
            out.append(ch)
            if ch == "\n":
                state = "code"

        elif state == "block_comment":
            out.append(ch)
            if ch == "*" and nxt == "/":
                i += 1
                out.append(region[i])
                state = "code"

        elif state == "string":
            out.append(ch)
            if ch == "\\":
                if i + 1 < len(region):
                    i += 1
                    out.append(region[i])
            elif ch == '"':
                state = "code"

        elif state == "char":
            out.append(ch)
            if ch == "\\":
                if i + 1 < len(region):
                    i += 1
                    out.append(region[i])
            elif ch == "'":
                state = "code"

        i += 1

    return text[:start] + "".join(out) + text[end:]


def unique_preserve_order(items: Sequence[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def build_function_rename_map(function_text: str, param_names: List[str], local_names: List[str], repo_words: Dict[str, str]) -> Dict[str, str]:
    existing_ids = collect_identifiers(function_text)
    rename_map: Dict[str, str] = {}
    used_targets: Set[str] = set()

    for old in unique_preserve_order(param_names + local_names):
        if old in CPP_KEYWORDS:
            continue
        new = pascalize_identifier(old, repo_words)
        if not new or new == old:
            continue
        if new in CPP_KEYWORDS:
            continue
        if new in used_targets and rename_map.get(old) != new:
            continue
        if new in existing_ids and new != old:
            continue
        rename_map[old] = new
        used_targets.add(new)
        existing_ids.discard(old)
        existing_ids.add(new)

    return rename_map


# ---------- Traitement fichier ----------

def iter_cpp_files(paths: Sequence[str]) -> List[pathlib.Path]:
    files: List[pathlib.Path] = []
    for raw in paths:
        path = pathlib.Path(raw)
        if path.is_file() and path.suffix.lower() in CPP_EXTS:
            files.append(path)
        elif path.is_dir():
            for f in path.rglob("*"):
                if f.is_file() and f.suffix.lower() in CPP_EXTS:
                    files.append(f)
    return sorted(set(files))


def process_cpp_text(text: str, repo_words: Dict[str, str]) -> Tuple[str, int, int]:
    functions = find_functions(text)
    if not functions:
        return text, 0, 0

    updated = text
    renamed_functions = 0
    renamed_identifiers = 0

    # On parcourt à l'envers pour ne pas casser les offsets.
    for fn in reversed(functions):
        param_inside = updated[fn.open_paren + 1:fn.close_paren]
        param_names: List[str] = []
        for part in split_top_level_commas(param_inside):
            parsed = parse_param(part)
            if parsed and parsed.kind == "named":
                param_names.append(parsed.name_part)

        body_inside = updated[fn.open_brace + 1:fn.close_brace]
        local_names = local_declared_names(body_inside)

        function_region_text = updated[fn.open_paren + 1:fn.close_brace]
        rename_map = build_function_rename_map(function_region_text, param_names, local_names, repo_words)
        if not rename_map:
            continue

        updated = replace_identifiers_in_region(updated, fn.open_paren + 1, fn.close_brace, rename_map)
        renamed_functions += 1
        renamed_identifiers += len(rename_map)

    return updated, renamed_functions, renamed_identifiers


def main() -> int:
    parser = argparse.ArgumentParser(description="Renomme en PascalCase les variables d'un .cpp à partir d'un répertoire JSON de mots")
    parser.add_argument("paths", nargs="+", help="Fichier(s) .cpp ou dossier(s) à traiter")
    parser.add_argument(
        "--repo",
        default="param_word_repository.json",
        help="Chemin du répertoire JSON produit par build_header_word_repository.py",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Écrit les changements dans les fichiers. Sinon: dry-run",
    )
    args = parser.parse_args()

    repo_path = pathlib.Path(args.repo)
    if not repo_path.exists():
        print(f"Répertoire introuvable: {repo_path}")
        return 1

    repo_words = load_repo_words(repo_path)
    if not repo_words:
        print(f"Répertoire vide: {repo_path}")
        return 1

    files = iter_cpp_files(args.paths)
    if not files:
        print("Aucun fichier .cpp trouvé.")
        return 1

    total_files = 0
    total_functions = 0
    total_identifiers = 0

    for path in files:
        original = path.read_text(encoding="utf-8", errors="ignore")
        updated, function_count, identifier_count = process_cpp_text(original, repo_words)
        if updated != original:
            total_files += 1
            total_functions += function_count
            total_identifiers += identifier_count
            if args.write:
                path.write_text(updated, encoding="utf-8", newline="")
                print(f"[updated] {path} ({identifier_count} renommage(s), {function_count} fonction(s))")
            else:
                print(f"[would change] {path} ({identifier_count} renommage(s), {function_count} fonction(s))")

    if total_files == 0:
        print("Aucun changement.")
    else:
        mode = "écrits" if args.write else "détectés"
        print(f"\n{total_files} fichier(s), {total_functions} fonction(s), {total_identifiers} renommage(s) {mode}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
