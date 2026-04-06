#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Aligne les paramètres dans les fichiers C/C++ puis synchronise les noms de
paramètres des headers à partir des définitions source.

Comportement typique :
- si on passe `monfichier.cpp`, le script :
  1) aligne les paramètres de `monfichier.cpp`,
  2) cherche les headers voisins (`monfichier.h`, `.hpp`, `.hh`, `.hxx`),
  3) synchronise les noms de paramètres de ces headers à partir du `.cpp`,
  4) aligne ensuite les paramètres des headers.

Exemples :
    python align_and_sync_cpp_header.py monfichier.cpp --write
    python align_and_sync_cpp_header.py monfichier.h --write
    python align_and_sync_cpp_header.py src/ --write --tab-size 4

Remarques :
- sans `--write`, le script fonctionne en dry-run ;
- le `--tab-size` doit correspondre à Visual Studio pour un alignement visuel correct ;
- il est conseillé de lancer le script sur un dépôt Git propre.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile
import types
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ALIGN_SOURCE = '#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n\n"""\nAligne les paramètres de fonctions C/C++ dans des fichiers .cpp/.h/.hpp.\n\nStyle appliqué :\n- le premier paramètre reste sur la même ligne que la parenthèse ouvrante,\n- chaque paramètre suivant passe sur sa propre ligne,\n- l\'indentation utilise un maximum de tabulations puis des espaces d\'ajustement,\n- les paramètres suivants sont alignés sous le premier paramètre,\n- les types sont alignés,\n- les noms sont alignés,\n- le séparateur entre type et nom utilise un maximum de tabulations puis des espaces si nécessaire,\n- la parenthèse fermante reste sur la même ligne que le dernier paramètre.\n\n\nUtilisation :\n    python align_cpp_params.py monfichier.cpp\n    python align_cpp_params.py monfichier.cpp --write\n    python align_cpp_params.py src include --ext .cpp .h .hpp --write\n    python align_cpp_params.py src --write --tab-size 4\n\nRemarques :\n- le script évite volontairement certains cas trop exotiques pour limiter le\n  risque de casser du code (pointeurs de fonction, syntaxes atypiques, etc.),\n- lance-le de préférence sur un dépôt Git propre,\n- vérifie toujours le diff après passage du script.\n"""\n\nimport argparse\nimport pathlib\nimport re\nfrom dataclasses import dataclass\nfrom typing import List, Optional, Tuple\n\n\nALLOWED_EXTS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}\n\n\n@dataclass\nclass ParsedParam:\n    original: str\n    kind: str\n    type_part: str = ""\n    name_part: str = ""\n    suffix: str = ""\n    default: str = ""\n\n\ndef visual_width(s: str, start_col: int = 0, tab_size: int = 4) -> int:\n    """Retourne la colonne visuelle après affichage de s."""\n    col = start_col\n    for ch in s:\n        if ch == "\\t":\n            advance = tab_size - (col % tab_size)\n            if advance == 0:\n                advance = tab_size\n            col += advance\n        else:\n            col += 1\n    return col\n\n\ndef next_tab_stop(col: int, tab_size: int) -> int:\n    """Retourne la prochaine tabulation visuelle stricte après col."""\n    advance = tab_size - (col % tab_size)\n    if advance == 0:\n        advance = tab_size\n    return col + advance\n\n\ndef make_padding_to_column(current_col: int, target_col: int, tab_size: int) -> str:\n    """\n    Construit un padding en utilisant un maximum de tabulations,\n    puis le minimum d\'espaces nécessaire pour atteindre exactement la colonne cible.\n    """\n    if target_col <= current_col:\n        return ""\n\n    col = current_col\n    out: List[str] = []\n\n    while next_tab_stop(col, tab_size) <= target_col:\n        col = next_tab_stop(col, tab_size)\n        out.append("\\t")\n\n    if col < target_col:\n        out.append(" " * (target_col - col))\n\n    return "".join(out)\n\n\ndef find_matching_paren(text: str, start: int) -> Optional[int]:\n    """Trouve la parenthèse fermante correspondant à text[start] == \'(\'."""\n    assert text[start] == "("\n    i = start + 1\n    depth = 1\n    state = "code"\n\n    while i < len(text):\n        ch = text[i]\n        nxt = text[i + 1] if i + 1 < len(text) else ""\n\n        if state == "code":\n            if ch == "/" and nxt == "/":\n                state = "line_comment"\n                i += 2\n                continue\n            if ch == "/" and nxt == "*":\n                state = "block_comment"\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "string"\n                i += 1\n                continue\n            if ch == "\'":\n                state = "char"\n                i += 1\n                continue\n\n            if ch == "(":\n                depth += 1\n            elif ch == ")":\n                depth -= 1\n                if depth == 0:\n                    return i\n\n        elif state == "line_comment":\n            if ch == "\\n":\n                state = "code"\n\n        elif state == "block_comment":\n            if ch == "*" and nxt == "/":\n                state = "code"\n                i += 2\n                continue\n\n        elif state == "string":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "code"\n\n        elif state == "char":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == "\'":\n                state = "code"\n\n        i += 1\n\n    return None\n\n\ndef skip_ws_comments(text: str, i: int) -> int:\n    """Saute les espaces et commentaires à partir de i."""\n    while i < len(text):\n        if text[i].isspace():\n            i += 1\n            continue\n\n        if text.startswith("//", i):\n            j = text.find("\\n", i + 2)\n            if j == -1:\n                return len(text)\n            i = j + 1\n            continue\n\n        if text.startswith("/*", i):\n            j = text.find("*/", i + 2)\n            if j == -1:\n                return len(text)\n            i = j + 2\n            continue\n\n        break\n\n    return i\n\n\ndef next_token(text: str, i: int) -> str:\n    """Retourne le prochain token simple après la position i."""\n    i = skip_ws_comments(text, i)\n    if i >= len(text):\n        return ""\n\n    if text.startswith("->", i):\n        return "->"\n    if text.startswith("[[", i):\n        return "[["\n    if text[i] in "{};:=" :\n        return text[i]\n\n    m = re.match(r"[A-Za-z_]\\w*", text[i:])\n    return m.group(0) if m else text[i]\n\n\ndef likely_function_signature(text: str, open_idx: int, close_idx: int) -> bool:\n    """Heuristique pour décider si (...) ressemble à une signature de fonction."""\n    line_start = text.rfind("\\n", 0, open_idx) + 1\n    prefix = text[line_start:open_idx].rstrip()\n\n    if not prefix:\n        return False\n\n    if re.search(r"\\b(?:if|for|while|switch|catch|return|sizeof|alignof|decltype|static_assert)\\s*$", prefix):\n        return False\n\n    if prefix.endswith("]"):\n        return False\n\n    if re.search(r"(?:\\.|->)\\s*[A-Za-z_~]\\w*\\s*$", prefix):\n        return False\n\n    token = next_token(text, close_idx + 1)\n    return token in {"{", ";", ":", "=", "const", "noexcept", "override", "final", "requires", "->", "[["}\n\n\ndef split_top_level_commas(s: str) -> List[str]:\n    """Découpe une liste d\'arguments sur les virgules de niveau supérieur."""\n    parts: List[str] = []\n    start = 0\n    depths = {"(": 0, "[": 0, "{": 0, "<": 0}\n    state = "code"\n    i = 0\n\n    while i < len(s):\n        ch = s[i]\n        nxt = s[i + 1] if i + 1 < len(s) else ""\n\n        if state == "code":\n            if ch == "/" and nxt == "/":\n                state = "line_comment"\n                i += 2\n                continue\n            if ch == "/" and nxt == "*":\n                state = "block_comment"\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "string"\n                i += 1\n                continue\n            if ch == "\'":\n                state = "char"\n                i += 1\n                continue\n\n            if ch == "(":\n                depths["("] += 1\n            elif ch == ")" and depths["("] > 0:\n                depths["("] -= 1\n            elif ch == "[":\n                depths["["] += 1\n            elif ch == "]" and depths["["] > 0:\n                depths["["] -= 1\n            elif ch == "{":\n                depths["{"] += 1\n            elif ch == "}" and depths["{"] > 0:\n                depths["{"] -= 1\n            elif ch == "<":\n                depths["<"] += 1\n            elif ch == ">" and depths["<"] > 0:\n                depths["<"] -= 1\n            elif ch == "," and all(v == 0 for v in depths.values()):\n                parts.append(s[start:i])\n                start = i + 1\n\n        elif state == "line_comment":\n            if ch == "\\n":\n                state = "code"\n\n        elif state == "block_comment":\n            if ch == "*" and nxt == "/":\n                state = "code"\n                i += 2\n                continue\n\n        elif state == "string":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "code"\n\n        elif state == "char":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == "\'":\n                state = "code"\n\n        i += 1\n\n    parts.append(s[start:])\n    return parts\n\n\ndef split_top_level_default(s: str) -> Tuple[str, str]:\n    """Sépare \'type nom\' et \'= valeur_par_defaut\' au niveau supérieur."""\n    depths = {"(": 0, "[": 0, "{": 0, "<": 0}\n    state = "code"\n    i = 0\n\n    while i < len(s):\n        ch = s[i]\n        nxt = s[i + 1] if i + 1 < len(s) else ""\n        prev = s[i - 1] if i > 0 else ""\n\n        if state == "code":\n            if ch == "/" and nxt == "/":\n                state = "line_comment"\n                i += 2\n                continue\n            if ch == "/" and nxt == "*":\n                state = "block_comment"\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "string"\n                i += 1\n                continue\n            if ch == "\'":\n                state = "char"\n                i += 1\n                continue\n\n            if ch == "(":\n                depths["("] += 1\n            elif ch == ")" and depths["("] > 0:\n                depths["("] -= 1\n            elif ch == "[":\n                depths["["] += 1\n            elif ch == "]" and depths["["] > 0:\n                depths["["] -= 1\n            elif ch == "{":\n                depths["{"] += 1\n            elif ch == "}" and depths["{"] > 0:\n                depths["{"] -= 1\n            elif ch == "<":\n                depths["<"] += 1\n            elif ch == ">" and depths["<"] > 0:\n                depths["<"] -= 1\n            elif ch == "=" and all(v == 0 for v in depths.values()) and prev not in "<>!=" and nxt != "=":\n                return s[:i], s[i:]\n\n        elif state == "line_comment":\n            if ch == "\\n":\n                state = "code"\n\n        elif state == "block_comment":\n            if ch == "*" and nxt == "/":\n                state = "code"\n                i += 2\n                continue\n\n        elif state == "string":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "code"\n\n        elif state == "char":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == "\'":\n                state = "code"\n\n        i += 1\n\n    return s, ""\n\n\ndef find_last_identifier_span(s: str) -> Optional[Tuple[int, int]]:\n    """Trouve le dernier identifiant de niveau supérieur dans un argument."""\n    depths = {"(": 0, "[": 0, "{": 0, "<": 0}\n    state = "code"\n    last = None\n    i = 0\n\n    while i < len(s):\n        ch = s[i]\n        nxt = s[i + 1] if i + 1 < len(s) else ""\n\n        if state == "code":\n            if ch == "/" and nxt == "/":\n                state = "line_comment"\n                i += 2\n                continue\n            if ch == "/" and nxt == "*":\n                state = "block_comment"\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "string"\n                i += 1\n                continue\n            if ch == "\'":\n                state = "char"\n                i += 1\n                continue\n\n            if ch == "(":\n                depths["("] += 1\n            elif ch == ")" and depths["("] > 0:\n                depths["("] -= 1\n            elif ch == "[":\n                depths["["] += 1\n            elif ch == "]" and depths["["] > 0:\n                depths["["] -= 1\n            elif ch == "{":\n                depths["{"] += 1\n            elif ch == "}" and depths["{"] > 0:\n                depths["{"] -= 1\n            elif ch == "<":\n                depths["<"] += 1\n            elif ch == ">" and depths["<"] > 0:\n                depths["<"] -= 1\n            elif all(v == 0 for v in depths.values()) and (ch.isalpha() or ch == "_"):\n                j = i + 1\n                while j < len(s) and (s[j].isalnum() or s[j] == "_"):\n                    j += 1\n                last = (i, j)\n                i = j\n                continue\n\n        elif state == "line_comment":\n            if ch == "\\n":\n                state = "code"\n\n        elif state == "block_comment":\n            if ch == "*" and nxt == "/":\n                state = "code"\n                i += 2\n                continue\n\n        elif state == "string":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "code"\n\n        elif state == "char":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == "\'":\n                state = "code"\n\n        i += 1\n\n    return last\n\n\ndef parse_param(raw: str) -> Optional[ParsedParam]:\n    """Essaie de découper un argument en type / nom / suffixe / défaut."""\n    s = raw.strip()\n\n    if not s:\n        return None\n\n    if s == "...":\n        return ParsedParam(original=raw, kind="ellipsis")\n\n    if s == "void":\n        return ParsedParam(original=raw, kind="other")\n\n    if "(*" in s or "(&" in s:\n        return None\n\n    base, default = split_top_level_default(s)\n    base = base.rstrip()\n\n    span = find_last_identifier_span(base)\n    if span is None:\n        return None\n\n    a, b = span\n    type_part = base[:a].rstrip()\n    name_part = base[a:b]\n    suffix = base[b:].rstrip()\n\n    if not type_part:\n        return None\n\n    prev = base[a - 1] if a > 0 else ""\n    if not (prev.isspace() or prev in "*&"):\n        return None\n\n    return ParsedParam(\n        original=raw,\n        kind="named",\n        type_part=type_part,\n        name_part=name_part,\n        suffix=suffix,\n        default=(" " + default.lstrip()) if default else "",\n    )\n\n\ndef make_param_separator(type_start_col: int, type_text: str, target_name_col: int, tab_size: int) -> str:\n    """\n    Construit un séparateur avec un maximum de tabulations,\n    puis des espaces si nécessaire pour finir l\'alignement.\n    """\n    type_end_col = visual_width(type_text, type_start_col, tab_size)\n    sep = make_padding_to_column(type_end_col, target_name_col, tab_size)\n\n    if "\\t" not in sep:\n        sep = "\\t" + sep\n\n    return sep\n\n\ndef format_named_param(\n    p: ParsedParam,\n    type_start_col: int,\n    target_name_col: int,\n    tab_size: int,\n) -> str:\n    sep = make_param_separator(type_start_col, p.type_part, target_name_col, tab_size)\n    return f"{p.type_part}{sep}{p.name_part}{p.suffix}{p.default}"\n\n\ndef format_parameter_block(text: str, open_idx: int, close_idx: int, tab_size: int) -> Optional[str]:\n    """Reformate le bloc de paramètres situé entre open_idx et close_idx."""\n    inside = text[open_idx + 1:close_idx]\n    if not inside.strip():\n        return None\n\n    parts = split_top_level_commas(inside)\n    if len(parts) < 2:\n        return None\n\n    parsed: List[ParsedParam] = []\n    named_count = 0\n\n    for part in parts:\n        p = parse_param(part)\n        if p is None:\n            return None\n        if p.kind == "named":\n            named_count += 1\n        parsed.append(p)\n\n    if named_count < 2:\n        return None\n\n    line_start = text.rfind("\\n", 0, open_idx) + 1\n    before_open = text[line_start:open_idx + 1]\n    line_end = text.find("\\n", line_start)\n    if line_end == -1:\n        line_end = len(text)\n    base_line = text[line_start:line_end]\n    base_indent = re.match(r"[ \\t]*", base_line).group(0)\n\n    anchor_col = visual_width(before_open, 0, tab_size)\n    base_indent_col = visual_width(base_indent, 0, tab_size)\n    continuation_indent = base_indent + make_padding_to_column(base_indent_col, anchor_col, tab_size)\n\n    named_params = [p for p in parsed if p.kind == "named"]\n    target_name_col = max(\n        next_tab_stop(anchor_col + visual_width(p.type_part, 0, tab_size), tab_size)\n        for p in named_params\n    )\n\n    rendered: List[str] = []\n    for idx, p in enumerate(parsed):\n        if p.kind == "named":\n            piece = format_named_param(p, anchor_col, target_name_col, tab_size)\n        elif p.kind == "ellipsis":\n            piece = "..."\n        else:\n            piece = p.original.strip()\n\n        if idx == 0:\n            rendered.append(piece)\n        else:\n            rendered.append("\\n" + continuation_indent + piece)\n\n    return ",".join(rendered)\n\n\ndef process_text(text: str, tab_size: int) -> Tuple[str, int]:\n    """Traite un fichier entier et retourne (texte_modifié, nombre_de_signatures)."""\n    out: List[str] = []\n    cursor = 0\n    i = 0\n    changes = 0\n\n    while i < len(text):\n        if text[i] == "(":\n            close_idx = find_matching_paren(text, i)\n            if close_idx is not None and likely_function_signature(text, i, close_idx):\n                replacement = format_parameter_block(text, i, close_idx, tab_size)\n                if replacement is not None:\n                    out.append(text[cursor:i + 1])\n                    out.append(replacement)\n                    cursor = close_idx\n                    i = close_idx\n                    changes += 1\n        i += 1\n\n    out.append(text[cursor:])\n    return "".join(out), changes\n\n\ndef iter_source_files(paths: List[str], extensions: List[str]) -> List[pathlib.Path]:\n    """Retourne la liste triée des fichiers source correspondant aux extensions."""\n    exts = set(e if e.startswith(".") else "." + e for e in extensions)\n    files: List[pathlib.Path] = []\n\n    for p in paths:\n        path = pathlib.Path(p)\n        if path.is_file():\n            if path.suffix.lower() in exts:\n                files.append(path)\n        elif path.is_dir():\n            for f in path.rglob("*"):\n                if f.is_file() and f.suffix.lower() in exts:\n                    files.append(f)\n\n    return sorted(set(files))\n\n\ndef main() -> int:\n    parser = argparse.ArgumentParser(\n        description="Aligne les paramètres de fonctions C/C++ dans les fichiers .cpp/.h"\n    )\n    parser.add_argument("paths", nargs="+", help="Fichier(s) ou dossier(s) à traiter")\n    parser.add_argument(\n        "--ext",\n        nargs="*",\n        default=sorted(ALLOWED_EXTS),\n        help="Extensions à traiter, ex: .cpp .h .hpp",\n    )\n    parser.add_argument(\n        "--write",\n        action="store_true",\n        help="Écrit les changements dans les fichiers. Sinon: dry-run",\n    )\n    parser.add_argument(\n        "--tab-size",\n        type=int,\n        default=4,\n        help="Largeur visuelle d\'une tabulation pour l\'alignement et les tab stops (défaut: 4)",\n    )\n\n    args = parser.parse_args()\n\n    if args.tab_size <= 0:\n        print("--tab-size doit être > 0")\n        return 1\n\n    files = iter_source_files(args.paths, args.ext)\n    if not files:\n        print("Aucun fichier trouvé.")\n        return 1\n\n    total_files = 0\n    total_changes = 0\n\n    for path in files:\n        original = path.read_text(encoding="utf-8", errors="ignore")\n        updated, changes = process_text(original, args.tab_size)\n\n        if changes > 0 and updated != original:\n            total_files += 1\n            total_changes += changes\n\n            if args.write:\n                path.write_text(updated, encoding="utf-8", newline="")\n                print(f"[updated] {path} ({changes} signature(s))")\n            else:\n                print(f"[would change] {path} ({changes} signature(s))")\n\n    if total_files == 0:\n        print("Aucun changement.")\n    else:\n        mode = "écrits" if args.write else "détectés"\n        print(f"\\n{total_files} fichier(s), {total_changes} signature(s) {mode}.")\n\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
SYNC_SOURCE = '#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n\n"""\nSynchronise les noms des paramètres dans les fichiers .h/.hpp à partir des\nfichiers .cpp/.cc/.cxx, en utilisant les définitions du .cpp comme référence.\n\nObjectif principal :\n- si dans le .cpp on a     : foo(double Test)\n- et dans le .h on a       : foo(double test = 0.0);\n- alors le .h devient      : foo(double Test = 0.0);\n\nLe script préserve au maximum le texte original du .h :\n- espaces / retours à la ligne,\n- valeurs par défaut déjà présentes,\n- formatage général de la déclaration.\n\nIl ne remplace que l\'identifiant du paramètre quand une correspondance fiable\nest trouvée.\n"""\n\nfrom __future__ import annotations\n\nimport argparse\nimport pathlib\nimport re\nfrom dataclasses import dataclass\nfrom typing import Dict, Iterable, List, Optional, Sequence, Tuple\n\nCPP_EXTS = {".c", ".cc", ".cpp", ".cxx"}\nHEADER_EXTS = {".h", ".hh", ".hpp", ".hxx"}\nCONTROL_KEYWORDS = {\n    "if",\n    "for",\n    "while",\n    "switch",\n    "catch",\n    "return",\n    "sizeof",\n    "alignof",\n    "decltype",\n    "static_assert",\n    "new",\n    "delete",\n}\nPURE_MODIFIERS = {\n    "const",\n    "volatile",\n    "constexpr",\n    "consteval",\n    "constinit",\n    "mutable",\n    "register",\n    "static",\n    "extern",\n    "inline",\n    "virtual",\n    "friend",\n    "struct",\n    "class",\n    "enum",\n    "typename",\n    "restrict",\n    "__restrict",\n    "__restrict__",\n    "_In_",\n    "_Out_",\n    "_Inout_",\n}\n\n\n@dataclass(frozen=True)\nclass ParsedParam:\n    raw: str\n    kind: str  # named / void / ellipsis / unsupported / unnamed\n    type_key: str = ""\n    name: str = ""\n    name_span: Optional[Tuple[int, int]] = None  # relative to raw\n\n\n@dataclass(frozen=True)\nclass FunctionRef:\n    name: str\n    key: Tuple[str, Tuple[str, ...]]\n    param_names: Tuple[str, ...]\n    source: str\n\n\n@dataclass(frozen=True)\nclass Replacement:\n    start: int\n    end: int\n    text: str\n\n\n# ---------------------------------------------------------------------------\n# Low level scanning helpers\n# ---------------------------------------------------------------------------\n\ndef find_matching_paren(text: str, start: int) -> Optional[int]:\n    if start < 0 or start >= len(text) or text[start] != "(":\n        return None\n\n    i = start + 1\n    depth = 1\n    state = "code"\n\n    while i < len(text):\n        ch = text[i]\n        nxt = text[i + 1] if i + 1 < len(text) else ""\n\n        if state == "code":\n            if ch == "/" and nxt == "/":\n                state = "line_comment"\n                i += 2\n                continue\n            if ch == "/" and nxt == "*":\n                state = "block_comment"\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "string"\n                i += 1\n                continue\n            if ch == "\'":\n                state = "char"\n                i += 1\n                continue\n            if ch == "(":\n                depth += 1\n            elif ch == ")":\n                depth -= 1\n                if depth == 0:\n                    return i\n\n        elif state == "line_comment":\n            if ch == "\\n":\n                state = "code"\n\n        elif state == "block_comment":\n            if ch == "*" and nxt == "/":\n                state = "code"\n                i += 2\n                continue\n\n        elif state == "string":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "code"\n\n        elif state == "char":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == "\'":\n                state = "code"\n\n        i += 1\n\n    return None\n\n\ndef skip_ws_comments(text: str, i: int) -> int:\n    while i < len(text):\n        if text[i].isspace():\n            i += 1\n            continue\n\n        if text.startswith("//", i):\n            j = text.find("\\n", i + 2)\n            if j == -1:\n                return len(text)\n            i = j + 1\n            continue\n\n        if text.startswith("/*", i):\n            j = text.find("*/", i + 2)\n            if j == -1:\n                return len(text)\n            i = j + 2\n            continue\n\n        break\n\n    return i\n\n\ndef scan_signature_terminator(text: str, start: int) -> Optional[str]:\n    """\n    Retourne \'{\' si la signature mène à une définition,\n    \';\' si elle mène à une déclaration,\n    None sinon.\n    """\n    i = start\n    paren = 0\n    bracket = 0\n    brace = 0\n    angle = 0\n    state = "code"\n\n    while i < len(text):\n        ch = text[i]\n        nxt = text[i + 1] if i + 1 < len(text) else ""\n\n        if state == "code":\n            if ch == "/" and nxt == "/":\n                state = "line_comment"\n                i += 2\n                continue\n            if ch == "/" and nxt == "*":\n                state = "block_comment"\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "string"\n                i += 1\n                continue\n            if ch == "\'":\n                state = "char"\n                i += 1\n                continue\n\n            if ch == "(":\n                paren += 1\n            elif ch == ")" and paren > 0:\n                paren -= 1\n            elif ch == "[":\n                bracket += 1\n            elif ch == "]" and bracket > 0:\n                bracket -= 1\n            elif ch == "<":\n                angle += 1\n            elif ch == ">" and angle > 0:\n                angle -= 1\n            elif ch == "{" and paren == 0 and bracket == 0 and brace == 0 and angle == 0:\n                return "{"\n            elif ch == ";" and paren == 0 and bracket == 0 and brace == 0 and angle == 0:\n                return ";"\n\n        elif state == "line_comment":\n            if ch == "\\n":\n                state = "code"\n\n        elif state == "block_comment":\n            if ch == "*" and nxt == "/":\n                state = "code"\n                i += 2\n                continue\n\n        elif state == "string":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "code"\n\n        elif state == "char":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == "\'":\n                state = "code"\n\n        i += 1\n\n    return None\n\n\ndef extract_function_name(text: str, open_idx: int) -> Optional[str]:\n    j = open_idx - 1\n    while j >= 0 and text[j].isspace():\n        j -= 1\n\n    if j < 0:\n        return None\n\n    end = j + 1\n    while j >= 0 and (text[j].isalnum() or text[j] in "_::~"):\n        j -= 1\n    start = j + 1\n    name = text[start:end]\n\n    if not name:\n        return None\n\n    bare = name.split("::")[-1]\n    if bare in CONTROL_KEYWORDS:\n        return None\n\n    if not re.match(r"^[A-Za-z_~]\\w*(?:::[A-Za-z_~]\\w*)*$", name):\n        return None\n\n    return name\n\n\n# ---------------------------------------------------------------------------\n# Parameter parsing\n# ---------------------------------------------------------------------------\n\ndef split_top_level_commas_with_spans(s: str) -> List[Tuple[str, int, int]]:\n    parts: List[Tuple[str, int, int]] = []\n    start = 0\n    depths = {"(": 0, "[": 0, "{": 0, "<": 0}\n    state = "code"\n    i = 0\n\n    while i < len(s):\n        ch = s[i]\n        nxt = s[i + 1] if i + 1 < len(s) else ""\n\n        if state == "code":\n            if ch == "/" and nxt == "/":\n                state = "line_comment"\n                i += 2\n                continue\n            if ch == "/" and nxt == "*":\n                state = "block_comment"\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "string"\n                i += 1\n                continue\n            if ch == "\'":\n                state = "char"\n                i += 1\n                continue\n\n            if ch == "(":\n                depths["("] += 1\n            elif ch == ")" and depths["("] > 0:\n                depths["("] -= 1\n            elif ch == "[":\n                depths["["] += 1\n            elif ch == "]" and depths["["] > 0:\n                depths["["] -= 1\n            elif ch == "{":\n                depths["{"] += 1\n            elif ch == "}" and depths["{"] > 0:\n                depths["{"] -= 1\n            elif ch == "<":\n                depths["<"] += 1\n            elif ch == ">" and depths["<"] > 0:\n                depths["<"] -= 1\n            elif ch == "," and all(v == 0 for v in depths.values()):\n                parts.append((s[start:i], start, i))\n                start = i + 1\n\n        elif state == "line_comment":\n            if ch == "\\n":\n                state = "code"\n\n        elif state == "block_comment":\n            if ch == "*" and nxt == "/":\n                state = "code"\n                i += 2\n                continue\n\n        elif state == "string":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "code"\n\n        elif state == "char":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == "\'":\n                state = "code"\n\n        i += 1\n\n    parts.append((s[start:], start, len(s)))\n    return parts\n\n\ndef split_top_level_default_index(s: str) -> Optional[int]:\n    depths = {"(": 0, "[": 0, "{": 0, "<": 0}\n    state = "code"\n    i = 0\n\n    while i < len(s):\n        ch = s[i]\n        nxt = s[i + 1] if i + 1 < len(s) else ""\n        prev = s[i - 1] if i > 0 else ""\n\n        if state == "code":\n            if ch == "/" and nxt == "/":\n                state = "line_comment"\n                i += 2\n                continue\n            if ch == "/" and nxt == "*":\n                state = "block_comment"\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "string"\n                i += 1\n                continue\n            if ch == "\'":\n                state = "char"\n                i += 1\n                continue\n\n            if ch == "(":\n                depths["("] += 1\n            elif ch == ")" and depths["("] > 0:\n                depths["("] -= 1\n            elif ch == "[":\n                depths["["] += 1\n            elif ch == "]" and depths["["] > 0:\n                depths["["] -= 1\n            elif ch == "{":\n                depths["{"] += 1\n            elif ch == "}" and depths["{"] > 0:\n                depths["{"] -= 1\n            elif ch == "<":\n                depths["<"] += 1\n            elif ch == ">" and depths["<"] > 0:\n                depths["<"] -= 1\n            elif ch == "=" and all(v == 0 for v in depths.values()) and prev not in "<>!=" and nxt != "=":\n                return i\n\n        elif state == "line_comment":\n            if ch == "\\n":\n                state = "code"\n\n        elif state == "block_comment":\n            if ch == "*" and nxt == "/":\n                state = "code"\n                i += 2\n                continue\n\n        elif state == "string":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "code"\n\n        elif state == "char":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == "\'":\n                state = "code"\n\n        i += 1\n\n    return None\n\n\ndef top_level_identifiers(s: str) -> List[Tuple[str, int, int]]:\n    ids: List[Tuple[str, int, int]] = []\n    depths = {"(": 0, "[": 0, "{": 0, "<": 0}\n    state = "code"\n    i = 0\n\n    while i < len(s):\n        ch = s[i]\n        nxt = s[i + 1] if i + 1 < len(s) else ""\n\n        if state == "code":\n            if ch == "/" and nxt == "/":\n                state = "line_comment"\n                i += 2\n                continue\n            if ch == "/" and nxt == "*":\n                state = "block_comment"\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "string"\n                i += 1\n                continue\n            if ch == "\'":\n                state = "char"\n                i += 1\n                continue\n\n            if ch == "(":\n                depths["("] += 1\n            elif ch == ")" and depths["("] > 0:\n                depths["("] -= 1\n            elif ch == "[":\n                depths["["] += 1\n            elif ch == "]" and depths["["] > 0:\n                depths["["] -= 1\n            elif ch == "{":\n                depths["{"] += 1\n            elif ch == "}" and depths["{"] > 0:\n                depths["{"] -= 1\n            elif ch == "<":\n                depths["<"] += 1\n            elif ch == ">" and depths["<"] > 0:\n                depths["<"] -= 1\n            elif all(v == 0 for v in depths.values()) and (ch.isalpha() or ch == "_"):\n                j = i + 1\n                while j < len(s) and (s[j].isalnum() or s[j] == "_"):\n                    j += 1\n                ids.append((s[i:j], i, j))\n                i = j\n                continue\n\n        elif state == "line_comment":\n            if ch == "\\n":\n                state = "code"\n\n        elif state == "block_comment":\n            if ch == "*" and nxt == "/":\n                state = "code"\n                i += 2\n                continue\n\n        elif state == "string":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == \'"\':\n                state = "code"\n\n        elif state == "char":\n            if ch == "\\\\":\n                i += 2\n                continue\n            if ch == "\'":\n                state = "code"\n\n        i += 1\n\n    return ids\n\n\ndef normalize_type_key(s: str) -> str:\n    return re.sub(r"\\s+", "", s)\n\n\ndef parse_param(raw: str) -> ParsedParam:\n    stripped = raw.strip()\n    if not stripped:\n        return ParsedParam(raw=raw, kind="unsupported")\n\n    if stripped == "...":\n        return ParsedParam(raw=raw, kind="ellipsis", type_key="...")\n\n    if stripped == "void":\n        return ParsedParam(raw=raw, kind="void", type_key="void")\n\n    if "(*" in stripped or "(&" in stripped:\n        return ParsedParam(raw=raw, kind="unsupported")\n\n    default_idx = split_top_level_default_index(raw)\n    base = raw if default_idx is None else raw[:default_idx]\n\n    ids = top_level_identifiers(base)\n    if not ids:\n        return ParsedParam(raw=raw, kind="unsupported")\n\n    _, a, b = ids[-1]\n    prev = base[a - 1] if a > 0 else ""\n    before = base[:a]\n    after = base[b:]\n    after_non_ws = after.lstrip()\n\n    # Le nom doit être séparé du type par un espace ou * / &.\n    if not prev or (not prev.isspace() and prev not in "*&"):\n        return ParsedParam(raw=raw, kind="unsupported")\n\n    # Après le nom, on tolère seulement fin de chaîne, espaces ou suffixe de tableau.\n    if after_non_ws and not after_non_ws.startswith("["):\n        return ParsedParam(raw=raw, kind="unsupported")\n\n    ids_before = [token for token, _, _ in ids[:-1]]\n    before_stripped = before.rstrip()\n    before_has_pointer_like = bool(before_stripped) and before_stripped[-1] in "*&>)]"\n\n    # Cas typique d\'un paramètre sans nom : "const MyType" ou "std::string"\n    # On évite de confondre le dernier identifiant du type avec un nom.\n    if not ids_before and not before_has_pointer_like:\n        return ParsedParam(raw=raw, kind="unnamed", type_key=normalize_type_key(base))\n\n    if ids_before and all(tok in PURE_MODIFIERS for tok in ids_before) and not before_has_pointer_like:\n        return ParsedParam(raw=raw, kind="unnamed", type_key=normalize_type_key(base))\n\n    type_text = base[:a] + base[b:]\n    return ParsedParam(\n        raw=raw,\n        kind="named",\n        type_key=normalize_type_key(type_text),\n        name=base[a:b],\n        name_span=(a, b),\n    )\n\n\ndef parse_parameter_list(inside: str) -> Optional[List[ParsedParam]]:\n    parts = split_top_level_commas_with_spans(inside)\n    parsed: List[ParsedParam] = []\n\n    # Cas foo(void)\n    if len(parts) == 1 and parts[0][0].strip() == "void":\n        return []\n\n    for raw, _start, _end in parts:\n        p = parse_param(raw)\n        if p.kind == "unsupported":\n            return None\n        parsed.append(p)\n\n    return parsed\n\n\n# ---------------------------------------------------------------------------\n# Function extraction\n# ---------------------------------------------------------------------------\n\ndef function_key(name: str, params: Sequence[ParsedParam]) -> Tuple[str, Tuple[str, ...]]:\n    return name, tuple(p.type_key for p in params if p.kind != "void")\n\n\ndef collect_cpp_references(path: pathlib.Path) -> List[FunctionRef]:\n    text = path.read_text(encoding="utf-8", errors="ignore")\n    refs: List[FunctionRef] = []\n    i = 0\n\n    while i < len(text):\n        if text[i] != "(":\n            i += 1\n            continue\n\n        close_idx = find_matching_paren(text, i)\n        if close_idx is None:\n            i += 1\n            continue\n\n        name = extract_function_name(text, i)\n        if not name:\n            i = close_idx + 1\n            continue\n\n        terminator = scan_signature_terminator(text, close_idx + 1)\n        if terminator != "{":\n            i = close_idx + 1\n            continue\n\n        inside = text[i + 1:close_idx]\n        params = parse_parameter_list(inside)\n        if params is None:\n            i = close_idx + 1\n            continue\n\n        # On ne garde que les fonctions où tous les paramètres ont bien un nom.\n        if any(p.kind not in {"named", "ellipsis"} for p in params):\n            i = close_idx + 1\n            continue\n\n        if any(p.kind == "ellipsis" for p in params):\n            i = close_idx + 1\n            continue\n\n        key = function_key(name, params)\n        refs.append(\n            FunctionRef(\n                name=name,\n                key=key,\n                param_names=tuple(p.name for p in params),\n                source=str(path),\n            )\n        )\n        i = close_idx + 1\n\n    return refs\n\n\ndef compute_header_replacements(path: pathlib.Path, ref_map: Dict[Tuple[str, Tuple[str, ...]], FunctionRef]) -> Tuple[List[Replacement], int]:\n    text = path.read_text(encoding="utf-8", errors="ignore")\n    replacements: List[Replacement] = []\n    i = 0\n    matched_functions = 0\n\n    while i < len(text):\n        if text[i] != "(":\n            i += 1\n            continue\n\n        close_idx = find_matching_paren(text, i)\n        if close_idx is None:\n            i += 1\n            continue\n\n        name = extract_function_name(text, i)\n        if not name:\n            i = close_idx + 1\n            continue\n\n        terminator = scan_signature_terminator(text, close_idx + 1)\n        if terminator != ";":\n            i = close_idx + 1\n            continue\n\n        inside = text[i + 1:close_idx]\n        parts = split_top_level_commas_with_spans(inside)\n        if len(parts) == 1 and parts[0][0].strip() == "void":\n            params: List[ParsedParam] = []\n        else:\n            params = []\n            for raw, start, end in parts:\n                parsed = parse_param(raw)\n                if parsed.kind == "unsupported":\n                    params = []\n                    break\n                if parsed.kind == "ellipsis":\n                    params = []\n                    break\n                # On n\'essaye pas d\'insérer un nom si le .h n\'en a pas déjà un.\n                # Le but ici est de renommer de façon sûre et non de reformater.\n                params.append(parsed)\n            else:\n                pass\n\n            if not parts:\n                params = []\n\n            # Si un paramètre est unsupported / ellipsis, on saute.\n            if parts and len(params) != len(parts):\n                i = close_idx + 1\n                continue\n\n        key = function_key(name, params)\n        ref = ref_map.get(key)\n        if ref is None:\n            i = close_idx + 1\n            continue\n\n        if len(ref.param_names) != len(params):\n            i = close_idx + 1\n            continue\n\n        matched_functions += 1\n\n        for ref_name, (raw, part_start, _part_end), header_param in zip(ref.param_names, parts, params):\n            if header_param.kind != "named":\n                continue\n            if header_param.name == ref_name:\n                continue\n            if header_param.name_span is None:\n                continue\n\n            name_start_rel, name_end_rel = header_param.name_span\n            abs_start = i + 1 + part_start + name_start_rel\n            abs_end = i + 1 + part_start + name_end_rel\n            replacements.append(Replacement(start=abs_start, end=abs_end, text=ref_name))\n\n        i = close_idx + 1\n\n    return replacements, matched_functions\n\n\n# ---------------------------------------------------------------------------\n# File helpers and main logic\n# ---------------------------------------------------------------------------\n\ndef iter_files(paths: Iterable[str], exts: Iterable[str]) -> List[pathlib.Path]:\n    wanted = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in exts}\n    out: List[pathlib.Path] = []\n\n    for raw in paths:\n        path = pathlib.Path(raw)\n        if path.is_file():\n            if path.suffix.lower() in wanted:\n                out.append(path)\n            continue\n        if path.is_dir():\n            for child in path.rglob("*"):\n                if child.is_file() and child.suffix.lower() in wanted:\n                    out.append(child)\n\n    return sorted(set(out))\n\n\ndef preferred_cpp_exts(exts: Iterable[str]) -> List[str]:\n    normalized = []\n    seen = set()\n    for ext in exts:\n        item = ext.lower() if ext.startswith(".") else f".{ext.lower()}"\n        if item not in seen:\n            normalized.append(item)\n            seen.add(item)\n\n    return sorted(normalized, key=lambda ext: (ext != ".cpp", ext))\n\n\ndef find_sibling_cpp_for_header(header_path: pathlib.Path, cpp_exts: Iterable[str]) -> Optional[pathlib.Path]:\n    for ext in preferred_cpp_exts(cpp_exts):\n        candidate = header_path.with_suffix(ext)\n        if candidate.is_file():\n            return candidate\n    return None\n\n\ndef derive_cpp_files_from_headers(\n    paths: Iterable[str],\n    header_exts: Iterable[str],\n    cpp_exts: Iterable[str],\n) -> Tuple[List[pathlib.Path], List[str]]:\n    header_wanted = {\n        ext.lower() if ext.startswith(".") else f".{ext.lower()}"\n        for ext in header_exts\n    }\n    derived: List[pathlib.Path] = []\n    notes: List[str] = []\n\n    for raw in paths:\n        path = pathlib.Path(raw)\n        if not path.is_file():\n            continue\n        if path.suffix.lower() not in header_wanted:\n            continue\n\n        sibling = find_sibling_cpp_for_header(path, cpp_exts)\n        if sibling is None:\n            notes.append(\n                f"Aucun fichier source voisin trouvé pour {path} "\n                f"(attendu: même nom de base avec extension .cpp/.cc/.cxx)."\n            )\n            continue\n\n        derived.append(sibling)\n        notes.append(f"[auto] {path} -> source de référence {sibling}")\n\n    return sorted(set(derived)), notes\n\n\ndef apply_replacements(text: str, replacements: Sequence[Replacement]) -> str:\n    if not replacements:\n        return text\n\n    ordered = sorted(replacements, key=lambda r: r.start, reverse=True)\n    result = text\n    last_start = None\n\n    for rep in ordered:\n        if last_start is not None and rep.end > last_start:\n            continue\n        result = result[:rep.start] + rep.text + result[rep.end:]\n        last_start = rep.start\n\n    return result\n\n\ndef build_reference_map(cpp_files: Sequence[pathlib.Path]) -> Tuple[Dict[Tuple[str, Tuple[str, ...]], FunctionRef], List[str]]:\n    ref_map: Dict[Tuple[str, Tuple[str, ...]], FunctionRef] = {}\n    warnings: List[str] = []\n\n    for path in cpp_files:\n        for ref in collect_cpp_references(path):\n            existing = ref_map.get(ref.key)\n            if existing is None:\n                ref_map[ref.key] = ref\n                continue\n\n            if existing.param_names != ref.param_names:\n                warnings.append(\n                    "Conflit pour {}{} entre {} et {} ; entrée ignorée.".format(\n                        ref.name,\n                        ref.key[1],\n                        existing.source,\n                        ref.source,\n                    )\n                )\n\n    return ref_map, warnings\n\n\ndef main() -> int:\n    parser = argparse.ArgumentParser(\n        description=(\n            "Renomme les paramètres dans les .h/.hpp pour qu\'ils portent les mêmes "\n            "noms que dans les définitions .cpp/.cc/.cxx correspondantes."\n        )\n    )\n    parser.add_argument(\n        "paths",\n        nargs="+",\n        help="Fichier(s) ou dossier(s) à analyser. Si on passe seulement un .h/.hpp, le script cherche automatiquement le .cpp homonyme dans le même dossier.",\n    )\n    parser.add_argument(\n        "--write",\n        action="store_true",\n        help="Écrit les changements dans les fichiers .h/.hpp ; sinon dry-run.",\n    )\n    parser.add_argument(\n        "--cpp-ext",\n        nargs="*",\n        default=sorted(CPP_EXTS),\n        help="Extensions de fichiers source à utiliser comme référence (.cpp/.cc/...)",\n    )\n    parser.add_argument(\n        "--header-ext",\n        nargs="*",\n        default=sorted(HEADER_EXTS),\n        help="Extensions de fichiers header à modifier (.h/.hpp/...)",\n    )\n\n    args = parser.parse_args()\n\n    cpp_files = iter_files(args.paths, args.cpp_ext)\n    header_files = iter_files(args.paths, args.header_ext)\n\n    if not header_files:\n        print("Aucun fichier .h/.hpp/.hh/.hxx trouvé.")\n        return 1\n\n    auto_notes: List[str] = []\n    if not cpp_files:\n        cpp_files, auto_notes = derive_cpp_files_from_headers(\n            args.paths,\n            args.header_ext,\n            args.cpp_ext,\n        )\n\n    if not cpp_files:\n        print("Aucun fichier .cpp/.cc/.cxx trouvé.")\n        if auto_notes:\n            print()\n            for note in auto_notes:\n                print(note)\n        return 1\n\n    ref_map, warnings = build_reference_map(cpp_files)\n    if not ref_map:\n        print("Aucune définition de fonction exploitable trouvée dans les fichiers source.")\n        return 1\n\n    total_files_changed = 0\n    total_name_replacements = 0\n    total_functions_matched = 0\n\n    for header_path in header_files:\n        replacements, matched_functions = compute_header_replacements(header_path, ref_map)\n        total_functions_matched += matched_functions\n        if not replacements:\n            continue\n\n        original = header_path.read_text(encoding="utf-8", errors="ignore")\n        updated = apply_replacements(original, replacements)\n        if updated == original:\n            continue\n\n        total_files_changed += 1\n        total_name_replacements += len(replacements)\n\n        if args.write:\n            header_path.write_text(updated, encoding="utf-8", newline="")\n            print(f"[updated] {header_path} ({len(replacements)} renommage(s))")\n        else:\n            print(f"[would change] {header_path} ({len(replacements)} renommage(s))")\n\n    if auto_notes:\n        print("\\nCorrespondances automatiques :")\n        for note in auto_notes:\n            print(f"- {note}")\n\n    if warnings:\n        print("\\nAvertissements :")\n        for warning in warnings:\n            print(f"- {warning}")\n\n    if total_files_changed == 0:\n        print("Aucun changement.")\n    else:\n        mode = "écrits" if args.write else "détectés"\n        print(\n            "\\n{} fichier(s) header modifié(s), {} renommage(s) {}, {} fonction(s) header appariée(s).".format(\n                total_files_changed,\n                total_name_replacements,\n                mode,\n                total_functions_matched,\n            )\n        )\n\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'


def load_embedded_namespace(source: str, name: str) -> Dict[str, object]:
    module = types.ModuleType(name)
    module.__file__ = f"<{name}>"
    sys.modules[name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module.__dict__


_ALIGN = load_embedded_namespace(ALIGN_SOURCE, "_embedded_align")
_SYNC = load_embedded_namespace(SYNC_SOURCE, "_embedded_sync")

DEFAULT_CPP_EXTS = sorted(_SYNC["CPP_EXTS"])
DEFAULT_HEADER_EXTS = sorted(_SYNC["HEADER_EXTS"])


@dataclass
class CountSummary:
    files: int = 0
    items: int = 0


@dataclass
class PipelineSummary:
    aligned_cpp: CountSummary
    synced_headers: CountSummary
    aligned_headers: CountSummary
    warnings: List[str]


def normalize_exts(exts: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for ext in exts:
        item = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        if item not in seen:
            seen.add(item)
            out.append(item)
    return sorted(out)


def iter_files_in_dir(root: pathlib.Path, exts: Sequence[str]) -> List[pathlib.Path]:
    wanted = set(normalize_exts(exts))
    out: List[pathlib.Path] = []
    for child in root.rglob("*"):
        if child.is_file() and child.suffix.lower() in wanted:
            out.append(child.resolve())
    return sorted(set(out))


def find_sibling_headers_for_cpp(cpp_path: pathlib.Path, header_exts: Sequence[str]) -> List[pathlib.Path]:
    out: List[pathlib.Path] = []
    for ext in normalize_exts(header_exts):
        candidate = cpp_path.with_suffix(ext)
        if candidate.is_file():
            out.append(candidate.resolve())
    return sorted(set(out))


def collect_targets(
    raw_paths: Sequence[str],
    cpp_exts: Sequence[str],
    header_exts: Sequence[str],
) -> Tuple[List[pathlib.Path], List[pathlib.Path], List[str]]:
    cpp_wanted = set(normalize_exts(cpp_exts))
    header_wanted = set(normalize_exts(header_exts))
    cpp_files = set()
    header_files = set()
    notes: List[str] = []

    find_sibling_cpp_for_header = _SYNC["find_sibling_cpp_for_header"]

    for raw in raw_paths:
        path = pathlib.Path(raw)
        if not path.exists():
            notes.append(f"[note] Introuvable : {path}")
            continue

        if path.is_dir():
            cpp_files.update(iter_files_in_dir(path, cpp_exts))
            header_files.update(iter_files_in_dir(path, header_exts))
            continue

        path = path.resolve()
        suffix = path.suffix.lower()

        if suffix in cpp_wanted:
            cpp_files.add(path)
            siblings = find_sibling_headers_for_cpp(path, header_exts)
            if siblings:
                header_files.update(siblings)
                joined = ", ".join(str(p) for p in siblings)
                notes.append(f"[auto] {path} -> header(s) voisin(s) : {joined}")
            continue

        if suffix in header_wanted:
            header_files.add(path)
            sibling_cpp = find_sibling_cpp_for_header(path, cpp_exts)
            if sibling_cpp is not None:
                sibling_cpp = pathlib.Path(sibling_cpp).resolve()
                cpp_files.add(sibling_cpp)
                notes.append(f"[auto] {path} -> source de référence : {sibling_cpp}")
            else:
                notes.append(f"[note] Aucun .cpp/.cc/.cxx voisin trouvé pour {path}")
            continue

        notes.append(f"[note] Extension ignorée : {path}")

    return sorted(cpp_files), sorted(header_files), notes


def copy_to_workspace(files: Sequence[pathlib.Path]) -> Tuple[tempfile.TemporaryDirectory[str], Dict[pathlib.Path, pathlib.Path]]:
    tmp = tempfile.TemporaryDirectory(prefix="align_sync_")
    root = pathlib.Path(tmp.name)
    mapping: Dict[pathlib.Path, pathlib.Path] = {}

    for idx, orig in enumerate(sorted(set(files))):
        work = root / f"{idx:04d}_{orig.name}"
        work.write_bytes(orig.read_bytes())
        mapping[orig] = work

    return tmp, mapping


def translate_warning_paths(text: str, display_map: Mapping[pathlib.Path, pathlib.Path]) -> str:
    translated = text
    for work, original in display_map.items():
        translated = translated.replace(str(work), str(original))
    return translated


def align_file_group(
    file_paths: Sequence[pathlib.Path],
    tab_size: int,
    dry_run: bool,
    display_map: Mapping[pathlib.Path, pathlib.Path],
    label: str,
) -> CountSummary:
    process_text = _ALIGN["process_text"]
    summary = CountSummary()

    for path in file_paths:
        original = path.read_text(encoding="utf-8", errors="ignore")
        updated, changes = process_text(original, tab_size)
        if changes > 0 and updated != original:
            path.write_text(updated, encoding="utf-8", newline="")
            summary.files += 1
            summary.items += changes
            verb = "would align" if dry_run else "aligned"
            shown = display_map.get(path, path)
            print(f"[{verb} {label}] {shown} ({changes} signature(s))")

    return summary


def sync_header_names(
    cpp_files: Sequence[pathlib.Path],
    header_files: Sequence[pathlib.Path],
    dry_run: bool,
    display_map: Mapping[pathlib.Path, pathlib.Path],
) -> Tuple[CountSummary, List[str]]:
    summary = CountSummary()
    warnings: List[str] = []

    if not cpp_files or not header_files:
        return summary, warnings

    build_reference_map = _SYNC["build_reference_map"]
    compute_header_replacements = _SYNC["compute_header_replacements"]
    apply_replacements = _SYNC["apply_replacements"]

    ref_map, ref_warnings = build_reference_map(cpp_files)
    warnings.extend(
        translate_warning_paths(warning, display_map) for warning in ref_warnings
    )

    if not ref_map:
        return summary, warnings

    for header_path in header_files:
        replacements, _matched = compute_header_replacements(header_path, ref_map)
        if not replacements:
            continue

        original = header_path.read_text(encoding="utf-8", errors="ignore")
        updated = apply_replacements(original, replacements)
        if updated == original:
            continue

        header_path.write_text(updated, encoding="utf-8", newline="")
        summary.files += 1
        summary.items += len(replacements)
        verb = "would sync" if dry_run else "synced"
        shown = display_map.get(header_path, header_path)
        print(f"[{verb} header] {shown} ({len(replacements)} renommage(s))")

    return summary, warnings


def run_pipeline(
    cpp_files: Sequence[pathlib.Path],
    header_files: Sequence[pathlib.Path],
    tab_size: int,
    dry_run: bool,
    display_map: Mapping[pathlib.Path, pathlib.Path],
) -> PipelineSummary:
    aligned_cpp = align_file_group(cpp_files, tab_size, dry_run, display_map, "cpp")
    synced_headers, warnings = sync_header_names(cpp_files, header_files, dry_run, display_map)
    aligned_headers = align_file_group(header_files, tab_size, dry_run, display_map, "header")
    return PipelineSummary(
        aligned_cpp=aligned_cpp,
        synced_headers=synced_headers,
        aligned_headers=aligned_headers,
        warnings=warnings,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Aligne les paramètres des fichiers C/C++ puis synchronise les noms "
            "de paramètres des headers à partir des définitions source."
        )
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help=(
            "Fichier(s) ou dossier(s) à traiter. "
            "Si on passe un .cpp, le script cherche automatiquement le header homonyme. "
            "Si on passe un .h/.hpp, le script cherche automatiquement le .cpp homonyme."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Écrit les changements dans les fichiers. Sinon: dry-run.",
    )
    parser.add_argument(
        "--tab-size",
        type=int,
        default=4,
        help="Largeur visuelle d'une tabulation pour l'alignement (défaut: 4).",
    )
    parser.add_argument(
        "--cpp-ext",
        nargs="*",
        default=DEFAULT_CPP_EXTS,
        help="Extensions source à traiter comme référence (.cpp/.cc/.cxx/.c).",
    )
    parser.add_argument(
        "--header-ext",
        nargs="*",
        default=DEFAULT_HEADER_EXTS,
        help="Extensions header à traiter (.h/.hpp/.hh/.hxx).",
    )

    args = parser.parse_args()

    if args.tab_size <= 0:
        print("--tab-size doit être > 0")
        return 1

    cpp_files, header_files, notes = collect_targets(args.paths, args.cpp_ext, args.header_ext)

    if not cpp_files and not header_files:
        print("Aucun fichier exploitable trouvé.")
        if notes:
            print()
            for note in notes:
                print(note)
        return 1

    all_files = sorted(set(cpp_files) | set(header_files))

    if args.write:
        display_map = {path: path for path in all_files}
        summary = run_pipeline(cpp_files, header_files, args.tab_size, False, display_map)
    else:
        tmp, mapping = copy_to_workspace(all_files)
        try:
            work_cpp = [mapping[p] for p in cpp_files]
            work_headers = [mapping[p] for p in header_files]
            display_map = {mapping[p]: p for p in all_files}
            summary = run_pipeline(work_cpp, work_headers, args.tab_size, True, display_map)
        finally:
            tmp.cleanup()

    if notes:
        print("\nCorrespondances / notes :")
        for note in notes:
            print(f"- {note}")

    if summary.warnings:
        print("\nAvertissements :")
        for warning in summary.warnings:
            print(f"- {warning}")

    total_changed_files = (
        summary.aligned_cpp.files
        + summary.synced_headers.files
        + summary.aligned_headers.files
    )
    total_items = (
        summary.aligned_cpp.items
        + summary.synced_headers.items
        + summary.aligned_headers.items
    )

    if total_changed_files == 0:
        print("Aucun changement.")
        return 0

    mode = "détectés" if not args.write else "écrits"
    print(
        "\nRésumé :\n"
        f"- cpp alignés        : {summary.aligned_cpp.files} fichier(s), {summary.aligned_cpp.items} signature(s)\n"
        f"- headers synchronisés: {summary.synced_headers.files} fichier(s), {summary.synced_headers.items} renommage(s)\n"
        f"- headers alignés    : {summary.aligned_headers.files} fichier(s), {summary.aligned_headers.items} signature(s)\n"
        f"- total              : {total_changed_files} opération(s) fichier, {total_items} changement(s) {mode}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
