#!/usr/bin/env python3
"""
repo_mapper.py — Tree-sitter + PageRank repo-map voor PR-Piet.

Leest de git diff tussen base en head van een pull request, extraheert
symbolen (functies/classes/methodes) uit de gewijzigde bestanden met
tree-sitter, bouwt een call-graph (callers/callees), rankt symbolen met een
lichte PageRank (Aider-stijl) en schrijft een gecomprimeerde context naar
.pr_piet/context.md voor PR-Agent.

Fail-safes:
  - context-overflow  -> truncate per sectie, laatste redmiddel: diff-only summary
  - geen gewijzigde bestanden -> leeg context.md (PR-Agent draait door zonder artifact)
  - niet-ondersteunde taal of parse-fout -> bestand wordt overgeslagen (nooit crash)

Gebruik (CI):
  scripts/repo_mapper.py --base <sha> --head <sha> [--output .pr_piet/context.md]

Alleen stdlib + tree-sitter. Zie requirements.txt voor pinned versies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# --------------------------------------------------------------------------
# Config (overridable via CLI)
# --------------------------------------------------------------------------

DEFAULT_OUTPUT = ".pr_piet/context.md"
DEFAULT_MAX_TOKENS = 4096  # max_context_tokens uit de spec
DEFAULT_AST_DEPTH = 3
# Was 102400 (uit de oorspronkelijke spec); verhoogd naar 512 KB na de
# productie-test op guardian-llmprovider-gateway PR #12 (+2803/-283, 214 KB):
# het volledige tree-sitter-pad duurde daar 2,0 s en de token-budget-truncatie
# is bewezen correct (fix a1633f8). De cap boundt alleen pathologische diffs.
DEFAULT_MAX_DIFF_BYTES = 524288
DEFAULT_LANGUAGES = ["python", "typescript", "go", "rust"]

# drop_large_files_pattern uit de spec (gitignore-stijl, relatief aan repo-root)
DEFAULT_IGNORE_PATTERNS = [
    "*.lock",
    "*-lock.json",
    "vendor/*",
    "dist/*",
    "build/*",
    "node_modules/*",
    "*.min.js",
    "*.map",
    "*.pb.go",
    "*.pb.ts",
]

SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".mjs": "typescript",
    ".cjs": "typescript",
    ".go": "go",
    ".rs": "rust",
}

# Node-types per taal: [definition-nodes (naam-dragend), call-nodes, naamextractie]
LANG_SPECS: Dict[str, Dict[str, Set[str]]] = {
    "python": {
        "defs": {
            "function_definition",
            "class_definition",
            "decorated_definition",
        },
        "calls": {"call"},
        "types": {"class_definition"},
    },
    "typescript": {
        "defs": {
            "function_declaration",
            "method_definition",
            "class_declaration",
            "generator_function_declaration",
            "abstract_class_declaration",
        },
        "calls": {"call_expression", "new_expression"},
        "types": {"class_declaration", "abstract_class_declaration", "interface_declaration"},
    },
    "go": {
        "defs": {"function_declaration", "method_declaration", "type_declaration"},
        "calls": {"call_expression"},
        "types": {"type_declaration"},
    },
    "rust": {
        "defs": {
            "function_item",
            "impl_item",
            "struct_item",
            "enum_item",
            "trait_item",
            "mod_item",
        },
        "calls": {"call_expression", "macro_invocation"},
        "types": {"struct_item", "enum_item", "trait_item"},
    },
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[repo_mapper] {msg}", file=sys.stderr)


def run_git(repo: Path, args: Sequence[str], check: bool = True) -> str:
    """Run a git command inside the repo; return stdout."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def load_language(name: str):
    """Load a tree-sitter Language by name; None if unavailable."""
    try:
        from tree_sitter import Language

        if name == "python":
            import tree_sitter_python

            return Language(tree_sitter_python.language())
        if name == "typescript":
            import tree_sitter_typescript

            # TSX voor .tsx/.jsx; fallback naar plain TS.
            return Language(tree_sitter_typescript.language_tsx())
        if name == "go":
            import tree_sitter_go

            return Language(tree_sitter_go.language())
        if name == "rust":
            import tree_sitter_rust

            return Language(tree_sitter_rust.language())
    except Exception as exc:  # pragma: no cover - alleen bij ontbrekende binding
        log(f"taal {name} niet beschikbaar: {exc}")
    return None


def parse_ast(language, source: bytes):
    from tree_sitter import Parser

    parser = Parser()
    parser.language = language
    return parser.parse(source)


# --------------------------------------------------------------------------
# Git diff-extractie
# --------------------------------------------------------------------------

class ChangeInfo:
    def __init__(self, status: str, old_path: str, new_path: str, additions: int = 0, deletions: int = 0):
        self.status = status
        self.old_path = old_path
        self.new_path = new_path
        self.additions = additions
        self.deletions = deletions

    @property
    def path(self) -> str:
        return self.new_path if self.new_path not in ("", "/dev/null") else self.old_path

    @property
    def is_deletion(self) -> bool:
        return self.status.startswith("D") or self.new_path in ("", "/dev/null")


def get_changed_files(repo: Path, base: str, head: str, ignore: Sequence[str]) -> List[ChangeInfo]:
    """Return changed files between base...head (three-dot = merge-base)."""
    # name-status met -M detecteert renames; numstat geeft regel-tellingen.
    try:
        name_status = run_git(repo, ["diff", "--name-status", "-M", f"{base}...{head}"])
    except RuntimeError:
        # Fallback: base is geen ancestor (bijv. force-push) -> two-dot
        name_status = run_git(repo, ["diff", "--name-status", "-M", f"{base}..{head}"])
    try:
        numstat = run_git(repo, ["diff", "--numstat", "-M", f"{base}...{head}"])
    except RuntimeError:
        numstat = run_git(repo, ["diff", "--numstat", "-M", f"{base}..{head}"])

    numstat_map: Dict[str, Tuple[int, int]] = {}
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            add, dele, path = parts[0], parts[1], parts[2]
            if add.isdigit() and dele.isdigit():
                numstat_map[path] = (int(add), int(dele))

    changes: List[ChangeInfo] = []
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        if status.startswith("R"):
            if len(parts) < 3:
                continue
            old_p, new_p = parts[1], parts[2]
        else:
            old_p = new_p = parts[1]
        if should_ignore(new_p, ignore) or should_ignore(old_p, ignore):
            continue
        add = dele = 0
        for cand in (new_p, old_p):
            if cand in numstat_map:
                add, dele = numstat_map[cand]
                break
        changes.append(ChangeInfo(status, old_p, new_p, add, dele))
    return changes


def should_ignore(path: str, ignore: Sequence[str]) -> bool:
    from fnmatch import fnmatch

    norm = path.lstrip("./")
    for pattern in ignore:
        pat = pattern.lstrip("./")
        if fnmatch(norm, pat) or fnmatch(norm, pat + "/**"):
            return True
        # pattern zonder slash matchen ook op basename (gitignore-achtig)
        if "/" not in pat and fnmatch(Path(norm).name, pat):
            return True
    return False


def total_diff_bytes(repo: Path, base: str, head: str) -> int:
    try:
        diff = run_git(repo, ["diff", "-M", f"{base}...{head}"])
    except RuntimeError:
        diff = run_git(repo, ["diff", "-M", f"{base}..{head}"])
    return len(diff.encode("utf-8"))


# --------------------------------------------------------------------------
# Symbol-extractie met tree-sitter
# --------------------------------------------------------------------------

class Symbol:
    __slots__ = ("name", "kind", "file", "line", "parent", "signature", "qualified")

    def __init__(self, name: str, kind: str, file: str, line: int, parent: Optional[str], signature: str):
        self.name = name
        self.kind = kind
        self.file = file
        self.line = line
        self.parent = parent
        self.signature = signature
        self.qualified = f"{parent}.{name}" if parent else name

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "parent": self.parent,
            "signature": self.signature,
            "qualified": self.qualified,
        }


class CallRef:
    __slots__ = ("caller", "callee", "line")

    def __init__(self, caller: str, callee: str, line: int):
        self.caller = caller
        self.callee = callee
        self.line = line


def _node_text(node, source: bytes, limit: int = 160) -> str:
    """Node source, single-line, truncated (signature-context)."""
    raw = bytes(node.text) if hasattr(node, "text") else source[node.start_byte:node.end_byte]
    text = raw.decode("utf-8", errors="replace")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _child_by_field(node, field: str):
    try:
        return node.child_by_field_name(field)
    except Exception:
        return None


def _name_of(node) -> Optional[str]:
    """Best-effort naam van een definitie-node, veld-gedreven."""
    name_node = _child_by_field(node, "name")
    if name_node is not None and name_node.type == "identifier":
        return bytes(name_node.text).decode("utf-8", errors="replace")
    # fallback: eerste identifier-kind
    for child in node.children:
        if child.type in ("identifier", "property_identifier", "type_identifier"):
            try:
                return bytes(child.text).decode("utf-8", errors="replace")
            except Exception:
                return None
    return None


def _call_name(node, source: bytes) -> Optional[str]:
    """Best-effort callee-naam van een call-node (functie of attribute-chain)."""
    func = _child_by_field(node, "function")
    if func is None:
        # python: callee is het eerste kind van type 'identifier' of 'attribute'
        for child in node.children:
            if child.type in ("identifier", "attribute"):
                func = child
                break
    if func is None:
        return None
    if func.type == "attribute":
        # a.b.c() -> laatste segment; parent object via velden
        parts = []
        cur = func
        while cur is not None and cur.type == "attribute":
            attr = _child_by_field(cur, "attribute")
            if attr is not None:
                try:
                    parts.append(bytes(attr.text).decode("utf-8", errors="replace"))
                except Exception:
                    pass
            cur = _child_by_field(cur, "object")
        if cur is not None:
            try:
                parts.append(bytes(cur.text).decode("utf-8", errors="replace"))
            except Exception:
                pass
        parts.reverse()
        if parts:
            # alleen laatste 2 segmenten: object.methode
            return ".".join(parts[-2:])
    try:
        return bytes(func.text).decode("utf-8", errors="replace")
    except Exception:
        return None


def extract_symbols(language_name: str, file_path: str, source: bytes, max_depth: int) -> Tuple[List[Symbol], List[CallRef]]:
    """Parse één bestand: lijst van definities + call-referenties (diepte-begrensd)."""
    language = load_language(language_name)
    if language is None:
        return [], []
    try:
        tree = parse_ast(language, source)
    except Exception as exc:
        log(f"parse-fout {file_path}: {exc}")
        return [], []
    spec = LANG_SPECS[language_name]
    defs: List[Symbol] = []
    calls: List[CallRef] = []
    stack: List[str] = []  # enclosing class/function-namen voor qualified names

    def walk(node, depth: int) -> None:
        if depth > max_depth + 6:  # harde cap, voorkomt pathologische AST's
            return
        ntype = node.type
        if ntype in spec["defs"]:
            name = _name_of(node)
            parent = stack[-1] if stack else None
            if name:
                kind = "class" if ntype in spec["types"] else "function"
                sig = _node_text(node, source)
                sym = Symbol(name, kind, file_path, node.start_point[0] + 1, parent, sig)
                defs.append(sym)
                stack.append(sym.qualified)
                for child in node.children:
                    walk(child, depth + 1)
                stack.pop()
                return
        if ntype in spec["calls"]:
            callee = _call_name(node, source)
            caller = stack[-1] if stack else None
            if callee:
                calls.append(CallRef(caller or "<module>", callee, node.start_point[0] + 1))
        for child in node.children:
            walk(child, depth + 1)

    walk(tree.root_node, 0)
    return defs, calls


# --------------------------------------------------------------------------
# PageRank (licht, Aider-stijl)
# --------------------------------------------------------------------------

def pagerank(graph: Dict[str, Set[str]], iters: int = 30, damping: float = 0.85) -> Dict[str, float]:
    """PageRank op gerichte call-graph (callee -> callers voor reverse-randen)."""
    nodes = set(graph.keys())
    for edges in graph.values():
        nodes.update(edges)
    if not nodes:
        return {}
    out_degree = {n: len(graph.get(n, set())) for n in nodes}
    rank = {n: 1.0 / len(nodes) for n in nodes}
    for _ in range(iters):
        new_rank: Dict[str, float] = {}
        dangling = sum(rank[n] for n, d in out_degree.items() if d == 0)
        for n in nodes:
            incoming = sum(
                rank[p] / out_degree[p]
                for p in graph
                if n in graph[p] and out_degree[p] > 0
            )
            new_rank[n] = (1 - damping) / len(nodes) + damping * (incoming + dangling / len(nodes))
        rank = new_rank
    return rank


# --------------------------------------------------------------------------
# Context-opbouw
# --------------------------------------------------------------------------

def approx_tokens(text: str) -> int:
    """Heuristiek: ~4 chars/token voor code (conservatief voor JSON/TOML)."""
    return max(1, len(text) // 4)


def build_context(
    changes: List[ChangeInfo],
    symbols: List[Symbol],
    calls: List[CallRef],
    rank: Dict[str, float],
    repo_root: Path,
    max_tokens: int,
) -> str:
    """Bouw context.md binnen token-budget; truncate per sectie."""
    parts: List[str] = []
    total_adds = sum(c.additions for c in changes)
    total_dels = sum(c.deletions for c in changes)

    header = [
        "# PR-Piet repo context",
        "",
        f"- Bestanden gewijzigd: **{len(changes)}** (+{total_adds}/-{total_dels} regels)",
        f"- Symbolen geïndexeerd: **{len(symbols)}**",
        f"- Call-referenties: **{len(calls)}**",
        "",
        "> Generated by scripts/repo_mapper.py (tree-sitter + PageRank).",
        "> Gebruik dit als repo-structuur-context naast de diff.",
        "",
    ]
    parts.append("\n".join(header))

    # --- gewijzigde bestanden ---
    files_section = ["## Gewijzigde bestanden", ""]
    for c in sorted(changes, key=lambda x: (x.additions + x.deletions), reverse=True)[:40]:
        files_section.append(f"- `{c.path}` ({c.status}, +{c.additions}/-{c.deletions})")
    files_section.append("")
    parts.append("\n".join(files_section))

    # --- definities per bestand ---
    by_file: Dict[str, List[Symbol]] = defaultdict(list)
    for s in symbols:
        by_file[s.file].append(s)

    # Belangrijkste bestanden eerst (hoogste churn), zodat de hard-truncate op
    # het token-budget de kern van de PR laat overleven in plaats van de
    # alfabetisch-eerste bestanden (gevonden op PR #12 guardian-llmprovider-gateway:
    # het kernbestand caretaker_runtime.py werd geknipt, manager.py bleef).
    churn_by_file: Dict[str, int] = defaultdict(int)
    for c in changes:
        churn_by_file[c.path] += c.additions + c.deletions
        if c.old_path != c.path:
            churn_by_file[c.old_path] += c.additions + c.deletions

    defs_section = ["## Gewijzigde symbolen", ""]
    for file in sorted(by_file, key=lambda f: (-churn_by_file.get(f, 0), f)):
        defs_section.append(f"### `{file}`")
        for s in sorted(by_file[file], key=lambda x: (x.line, x.name)):
            defs_section.append(f"- {s.kind} **{s.qualified}** (regel {s.line}): {s.signature}")
        defs_section.append("")
    parts.append("\n".join(defs_section))

    # --- top-callers (wie roept gewijzigde code aan) ---
    changed_qualified = {s.qualified for s in symbols}
    caller_of_changed: Dict[str, Set[str]] = defaultdict(set)
    callee_edges: Dict[str, Set[str]] = defaultdict(set)
    for call in calls:
        callee_edges[call.caller].add(call.callee)
        if call.callee in changed_qualified or any(call.callee.endswith("." + q) or q.endswith("." + call.callee) for q in changed_qualified):
            caller_of_changed[call.caller].add(call.callee)

    callers_section = ["## Callers van gewijzigde code", ""]
    ranked_callers = sorted(caller_of_changed.items(), key=lambda kv: rank.get(kv[0], 0.0), reverse=True)[:25]
    if ranked_callers:
        for caller, callees in ranked_callers:
            callers_section.append(f"- **{caller}** roept: {', '.join(sorted(callees)[:6])}")
    else:
        callers_section.append("_Geen directe callers gevonden in deze diff._")
    callers_section.append("")
    parts.append("\n".join(callers_section))

    # --- top-callees (waar gewijzigde code op leunt) ---
    callees_section = ["## Callees van gewijzigde code (top-ranked)", ""]
    changed_caller_edges: Dict[str, Set[str]] = {
        c: edges for c, edges in callee_edges.items() if c in changed_qualified
    }
    callee_ranks: Dict[str, float] = {}
    for callee in sorted({c for edges in changed_caller_edges.values() for c in edges}):
        callee_ranks[callee] = sum(rank.get(c, 0.0) for c in changed_caller_edges if callee in changed_caller_edges[c])
    top_callees = sorted(callee_ranks.items(), key=lambda kv: kv[1], reverse=True)[:25]
    if top_callees:
        for callee, _ in top_callees:
            callers_list = [c for c, edges in changed_caller_edges.items() if callee in edges][:4]
            callees_section.append(f"- **{callee}** (via: {', '.join(callers_list)})")
    else:
        callees_section.append("_Geen callees buiten de diff gevonden._")
    callees_section.append("")
    parts.append("\n".join(callees_section))

    # --- token-budget: truncate van onderaf (fail-safe) ---
    # Sectie-indexen: 0=header, 1=files, 2=defs, 3=callers, 4=callees
    full = "\n".join(parts)
    if approx_tokens(full) <= max_tokens:
        return full

    log(f"context {approx_tokens(full)} tokens > budget {max_tokens}; truncaten")
    # Schrapvolgorde: callees -> callers -> defs-regels -> files-regels
    drop_order = [4, 3]  # callees en callers zijn het eerste weg
    kept_parts = [parts[0]]  # header blijft altijd
    for idx in range(1, len(parts)):
        if idx in drop_order:
            continue
        kept_parts.append(parts[idx])
    # Hard truncate per resterende sectie tot het budget past
    budget = max_tokens
    result_parts: List[str] = []
    used = 0
    for part in kept_parts:
        part_tokens = approx_tokens(part)
        if used + part_tokens > budget:
            lines = part.splitlines()
            kept: List[str] = []
            for line in lines:
                candidate = "\n".join(kept + [line])
                # +12 tokens reserve voor de truncate-marker, zodat een
                # ingekorte sectie GARANDEERD binnen (budget - used) blijft.
                # Zonder die reserve kon de check hieronder de héle sectie
                # weggooien wanneer de inkorting 1-12 tokens overschreed
                # (regressie gevonden op PR #12 guardian-llmprovider-gateway).
                if used + approx_tokens(candidate) + 12 <= budget:
                    kept.append(line)
                else:
                    break
            if kept:
                kept.append("_... (verder ingekort wegens token-budget)_")
                part = "\n".join(kept)
            else:
                part = ""
        if used + approx_tokens(part) > budget:
            break
        result_parts.append(part)
        used += approx_tokens(part)

    truncated = "\n".join(result_parts)
    if approx_tokens(truncated) > max_tokens:  # laatste redmiddel (fail-safe)
        log("context nog steeds te groot; diff-only summary")
        summary = [
            "# PR-Piet repo context (diff-only summary)",
            "",
            f"- {len(changes)} bestanden gewijzigd (+{total_adds}/-{total_dels})",
            "- Symbool-context overgeslagen wegens token-budget.",
            "",
        ]
        for c in sorted(changes, key=lambda x: (x.additions + x.deletions), reverse=True)[:40]:
            summary.append(f"- `{c.path}` (+{c.additions}/-{c.deletions})")
        truncated = "\n".join(summary)
    return truncated


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PR-Piet repo mapper (tree-sitter + PageRank)")
    parser.add_argument("--repo", default=".", help="pad naar de git-repo (default: .)")
    parser.add_argument("--base", required=True, help="base sha (meestal merge-base / target branch)")
    parser.add_argument("--head", required=True, help="head sha (PR-branch)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="uitvoer-pad voor context.md")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="token-budget")
    parser.add_argument("--ast-depth", type=int, default=DEFAULT_AST_DEPTH, help="AST-diepte (defs/calls)")
    parser.add_argument("--max-diff-bytes", type=int, default=DEFAULT_MAX_DIFF_BYTES, help="max diff-grootte")
    parser.add_argument("--languages", default=",".join(DEFAULT_LANGUAGES), help="komma-gescheiden talen")
    parser.add_argument("--ignore", action="append", default=[], help="extra ignore-pattern (repeatable)")
    parser.add_argument("--json", action="store_true", help="schrijf ook symbolen naar <output>.symbols.json")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    output = Path(args.output)
    if not output.is_absolute():
        output = repo / output
    output.parent.mkdir(parents=True, exist_ok=True)

    ignore = list(DEFAULT_IGNORE_PATTERNS) + args.ignore
    languages = {lang.strip() for lang in args.languages.split(",") if lang.strip()}

    # --- diff-check: oversized diff? ---
    diff_bytes = total_diff_bytes(repo, args.base, args.head)
    log(f"diff-grootte: {diff_bytes} bytes (max {args.max_diff_bytes})")
    if diff_bytes > args.max_diff_bytes:
        log(f"diff > {args.max_diff_bytes} bytes; context wordt diff-only summary (fail-safe)")
        changes = get_changed_files(repo, args.base, args.head, ignore)
        total_adds = sum(c.additions for c in changes)
        total_dels = sum(c.deletions for c in changes)
        summary = [
            "# PR-Piet repo context (diff-only summary)",
            "",
            f"- {len(changes)} bestanden gewijzigd (+{total_adds}/-{total_dels})",
            "- AST-context overgeslagen: diff overschrijdt max_diff_bytes.",
            "",
        ]
        for c in sorted(changes, key=lambda x: (x.additions + x.deletions), reverse=True)[:40]:
            summary.append(f"- `{c.path}` (+{c.additions}/-{c.deletions})")
        output.write_text("\n".join(summary) + "\n", encoding="utf-8")
        log(f"geschreven: {output} ({approx_tokens(chr(10).join(summary))} tokens)")
        return 0

    changes = get_changed_files(repo, args.base, args.head, ignore)
    log(f"gewijzigde bestanden: {len(changes)}")

    # --- lees bestanden uit HEAD (niet werktree!) ---
    symbols: List[Symbol] = []
    calls: List[CallRef] = []
    for change in changes:
        if change.is_deletion:
            continue
        ext = Path(change.path).suffix
        lang = SUPPORTED_EXTENSIONS.get(ext)
        if lang is None or lang not in languages:
            continue
        try:
            source = run_git(repo, ["show", f"{args.head}:{change.path}"])
        except RuntimeError:
            log(f"skip {change.path}: niet leesbaar op {args.head[:8]}")
            continue
        file_symbols, file_calls = extract_symbols(lang, change.path, source.encode("utf-8"), args.ast_depth)
        symbols.extend(file_symbols)
        calls.extend(file_calls)
        log(f"  {change.path}: {len(file_symbols)} symbols, {len(file_calls)} calls")

    # --- call-graph + PageRank ---
    graph: Dict[str, Set[str]] = defaultdict(set)
    for call in calls:
        graph[call.caller].add(call.callee)
    rank = pagerank(dict(graph))
    top = sorted(rank.items(), key=lambda kv: kv[1], reverse=True)[:10]
    log("top-ranked: " + ", ".join(f"{n}({v:.3f})" for n, v in top))

    context = build_context(changes, symbols, calls, rank, repo, args.max_tokens)
    output.write_text(context + "\n", encoding="utf-8")
    log(f"geschreven: {output} ({approx_tokens(context)} tokens)")

    if args.json:
        json_out = output.with_suffix(output.suffix + ".symbols.json")
        json_out.write_text(
            json.dumps(
                {
                    "symbols": [s.to_dict() for s in symbols],
                    "calls": [
                        {"caller": c.caller, "callee": c.callee, "line": c.line}
                        for c in calls
                    ],
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        log(f"symbolen: {json_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
