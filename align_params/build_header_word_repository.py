#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Construit et enrichit un répertoire de mots à partir des noms de paramètres
trouvés dans des fichiers .h/.hpp/.hh/.hxx.

Le répertoire est cumulatif : si le fichier JSON existe déjà,
les nouveaux mots sont ajoutés sans effacer les anciens.

Le script lit aussi un fichier ban.txt situé dans le même répertoire que ce
script. Ce fichier contient un mot banni par ligne. Les mots bannis ne sont
pas ajoutés au JSON et, s'ils y existent déjà, ils en sont supprimés.

Utilisation :
    python build_header_word_repository.py monfichier.h
    python build_header_word_repository.py include/ autre.h --repo param_word_repository.json
    python build_header_word_repository.py include/ --write-report
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple


HEADER_EXTS = {".h", ".hh", ".hpp", ".hxx"}


@dataclass
class ParsedParam:
    original: str
    kind: str
    type_part: str = ""
    name_part: str = ""
    suffix: str = ""
    default: str = ""


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


def iter_header_files(paths: List[str]) -> List[pathlib.Path]:
    files: List[pathlib.Path] = []

    for raw in paths:
        path = pathlib.Path(raw)
        if path.is_file() and path.suffix.lower() in HEADER_EXTS:
            files.append(path)
        elif path.is_dir():
            for f in path.rglob("*"):
                if f.is_file() and f.suffix.lower() in HEADER_EXTS:
                    files.append(f)

    return sorted(set(files))


def split_camel_and_snake(identifier: str) -> List[str]:
    parts: List[str] = []
    for chunk in re.split(r"[^A-Za-z0-9]+", identifier):
        if not chunk:
            continue
        parts.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+", chunk))
    return parts


def canonical_word(word: str) -> str:
    if word.isdigit():
        return word
    return word[:1].upper() + word[1:].lower()


def words_from_identifier(identifier: str) -> List[str]:
    name = identifier.strip()
    if re.match(r"(?i)^xl_", name):
        name = name[3:]

    words = split_camel_and_snake(name)
    return [canonical_word(w) for w in words if w and w.lower() != "xl"]


def load_repo(repo_path: pathlib.Path) -> Dict[str, object]:
    if not repo_path.exists():
        return {
            "version": 1,
            "updated_at": None,
            "sources": [],
            "words": {},
            "counts": {},
        }

    data = json.loads(repo_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Répertoire invalide: {repo_path}")

    data.setdefault("version", 1)
    data.setdefault("updated_at", None)
    data.setdefault("sources", [])
    data.setdefault("words", {})
    data.setdefault("counts", {})
    return data


def save_repo(repo_path: pathlib.Path, repo: Dict[str, object]) -> None:
    repo["updated_at"] = datetime.now(timezone.utc).isoformat()
    repo_path.write_text(json.dumps(repo, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def load_banned_words() -> Set[str]:
    ban_path = pathlib.Path(__file__).resolve().parent / "ban.txt"
    if not ban_path.exists():
        return set()

    banned: Set[str] = set()
    for line in ban_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        word = line.strip()
        if not word or word.startswith("#"):
            continue
        banned.add(word.lower())
    return banned


def purge_banned_words(words_map: Dict[str, str], counts: Dict[str, int], banned_words: Set[str]) -> int:
    removed = 0
    for banned in list(banned_words):
        if banned in words_map:
            del words_map[banned]
            removed += 1
        if banned in counts:
            del counts[banned]
    return removed


def extract_param_names_from_header(path: pathlib.Path) -> List[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    names: List[str] = []
    i = 0

    while i < len(text):
        if text[i] == "(":
            close_idx = find_matching_paren(text, i)
            if close_idx is not None and likely_function_signature(text, i, close_idx):
                inside = text[i + 1:close_idx]
                parts = split_top_level_commas(inside)
                for part in parts:
                    parsed = parse_param(part)
                    if parsed and parsed.kind == "named" and parsed.name_part:
                        names.append(parsed.name_part)
                i = close_idx
        i += 1

    return names


def main() -> int:
    parser = argparse.ArgumentParser(description="Construit un répertoire cumulatif de mots à partir de fichiers header C/C++")
    parser.add_argument("paths", nargs="+", help="Fichier(s) .h ou dossier(s) à analyser")
    parser.add_argument(
        "--repo",
        default="param_word_repository.json",
        help="Chemin du fichier JSON de répertoire (défaut: param_word_repository.json)",
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="Écrit aussi un fichier texte listant les mots triés, à côté du JSON",
    )
    args = parser.parse_args()

    files = iter_header_files(args.paths)
    if not files:
        print("Aucun fichier header trouvé.")
        return 1

    repo_path = pathlib.Path(args.repo)
    repo = load_repo(repo_path)
    words_map: Dict[str, str] = dict(repo.get("words", {}))
    counts: Dict[str, int] = {k: int(v) for k, v in dict(repo.get("counts", {})).items()}
    sources = set(str(s) for s in repo.get("sources", []))
    banned_words = load_banned_words()

    removed_existing = purge_banned_words(words_map, counts, banned_words)
    skipped_banned = 0
    total_params = 0
    added_words = 0

    for file_path in files:
        param_names = extract_param_names_from_header(file_path)
        total_params += len(param_names)
        sources.add(str(file_path))

        for name in param_names:
            for word in words_from_identifier(name):
                key = word.lower()
                if not key:
                    continue
                if key in banned_words:
                    skipped_banned += 1
                    continue
                if key not in words_map:
                    words_map[key] = word
                    added_words += 1
                counts[key] = counts.get(key, 0) + 1

    removed_after_merge = purge_banned_words(words_map, counts, banned_words)

    repo["words"] = dict(sorted(words_map.items(), key=lambda kv: kv[0]))
    repo["counts"] = dict(sorted(counts.items(), key=lambda kv: kv[0]))
    repo["sources"] = sorted(sources)
    save_repo(repo_path, repo)

    print(f"Répertoire mis à jour : {repo_path}")
    print(f"Headers analysés : {len(files)}")
    print(f"Paramètres trouvés : {total_params}")
    print(f"Nouveaux mots ajoutés : {added_words}")
    print(f"Total mots distincts : {len(words_map)}")
    if banned_words:
        print(f"Mots bannis chargés : {len(banned_words)}")
        print(f"Mots bannis ignorés pendant l'analyse : {skipped_banned}")
        print(f"Mots bannis supprimés du JSON : {removed_existing + removed_after_merge}")
    else:
        print("Aucun ban.txt trouvé ou aucun mot banni chargé.")

    if args.write_report:
        report_path = repo_path.with_suffix(".txt")
        words_sorted = [repo["words"][k] for k in sorted(repo["words"])]
        report_path.write_text("\n".join(words_sorted) + "\n", encoding="utf-8")
        print(f"Rapport texte écrit : {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
