"""Locating code: search, layout, symbols, impact.

One tool covers every "where is it" question, so there is only one thing to
choose from when the next step is to look for something.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fastmcp import FastMCP

from code_engine.finder import find_symbols_in_file, list_classes, list_functions
from code_engine.languages import LANGUAGES
from config import resolve_path
from core import impact as impact_analysis
from core import search as text_search
from ._common import rel

ACTIONS = ("search", "tree", "files", "symbols", "impact")


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def find(
        action: str = "search",
        query: str = "",
        workspace: str = "",
        path: str = ".",
        paths: list[str] | None = None,
        regex: bool = False,
        case_sensitive: bool = True,
        file_glob: str = "",
        context_lines: int = 2,
        max_results: int = 100,
        max_depth: int = 3,
        definitions_only: bool = False,
        include_deps: bool = False,
    ) -> dict:
        """Find code. Give workspace='<alias>' and/or an absolute path.

        action:
          search  (default) every occurrence of `query`, grouped by file, with
                  context lines. Definitions are marked using tree-sitter.
                  regex=True to search a pattern, file_glob='*.dart' to narrow.
          tree    directory layout under `path`
          files   file paths under `path`
          symbols functions and classes in a file or directory
          impact  what else a change to `paths` may affect: reverse imports,
                  call sites, related migrations/schemas/routes/tests
        """
        action = (action or "search").strip().lower()
        if action not in ACTIONS:
            raise ValueError(f"action must be one of: {', '.join(ACTIONS)}")

        base = resolve_path(workspace, path)
        if not base.exists():
            raise FileNotFoundError(str(base))
        root = base if base.is_dir() else base.parent
        exclude_deps = not include_deps

        if action == "search":
            return _search(root, query, regex, case_sensitive, max_results,
                           context_lines, exclude_deps, file_glob, definitions_only)

        if action == "tree":
            return _tree(root, max_depth, max_results, exclude_deps)

        if action == "files":
            return _files(root, max_results, exclude_deps)

        if action == "symbols":
            targets = [resolve_path(workspace, item) for item in paths] if paths else [base]
            return _symbols(root, targets, max_results, exclude_deps)

        targets = paths or ([rel(root, base)] if base.is_file() else [])
        if not targets:
            raise ValueError("impact needs paths=['file', ...]")
        return impact_analysis.analyze(root, [rel(root, resolve_path(workspace, item)) for item in targets])


# ------------------------------------------------------------------- actions

def _search(root: Path, query: str, regex: bool, case_sensitive: bool, max_results: int,
            context_lines: int, exclude_deps: bool, file_glob: str,
            definitions_only: bool) -> dict:
    query = (query or "").strip()
    if not query:
        raise ValueError("query is required for action='search'")

    pattern = query if regex else rf"\b{_escape(query)}\b"
    found = text_search.search(
        root, pattern,
        regex=True,
        case_sensitive=case_sensitive,
        max_results=max_results,
        context_lines=max(0, context_lines),
        exclude_deps=exclude_deps,
        globs=[file_glob] if file_glob else None,
    )
    results = found["results"]

    matches: dict[str, list[dict]] = {}
    for hit in results:
        entry = {"line": hit["line"]}
        entry["code" if hit.get("context") else "text"] = hit.get("context") or hit["text"]
        matches.setdefault(hit["file"], []).append(entry)

    definitions = [] if regex else _definitions(root, list(matches), query)
    if definitions_only and definitions:
        keep = {item["file"] for item in definitions}
        matches = {name: hits for name, hits in matches.items() if name in keep}

    payload = {
        "query": query,
        "count": sum(len(hits) for hits in matches.values()),
        "files": len(matches),
        "matches": matches,
    }
    if definitions:
        payload["definitions"] = definitions
    if len(results) >= max_results:
        payload["truncated"] = True
    if not results:
        payload["note"] = (
            "No hits. Try case_sensitive=False, a shorter fragment, regex=True, "
            "or include_deps=True."
        )
    return payload


def _tree(root: Path, max_depth: int, max_entries: int, exclude_deps: bool) -> dict:
    lines = [root.name + "/"]
    count = 0

    def walk(folder: Path, depth: int = 0) -> None:
        nonlocal count
        if depth >= max_depth or count >= max_entries:
            return
        try:
            entries = sorted(folder.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        except OSError:
            return
        for item in entries:
            if exclude_deps and item.name in text_search.DEPENDENCY_DIRS:
                continue
            lines.append(f"{'    ' * (depth + 1)}{item.name}{'/' if item.is_dir() else ''}")
            count += 1
            if count >= max_entries:
                return
            if item.is_dir():
                walk(item, depth + 1)

    walk(root)
    return {"root": str(root), "tree": "\n".join(lines), "count": count}


def _files(root: Path, max_entries: int, exclude_deps: bool) -> dict:
    files: list[str] = []
    for file_path in text_search.iter_files(root, exclude_deps):
        try:
            files.append(file_path.relative_to(root).as_posix())
        except ValueError:
            continue
        if len(files) >= max_entries:
            break
    return {"root": str(root), "files": files, "count": len(files),
            "truncated": len(files) >= max_entries}


def _symbols(root: Path, targets: list[Path], max_entries: int, exclude_deps: bool) -> dict:
    candidates: list[Path] = []
    for target in targets:
        if target.is_dir():
            candidates.extend(
                file_path for file_path in text_search.iter_files(target, exclude_deps)
                if file_path.suffix.lower() in LANGUAGES
            )
        elif target.suffix.lower() in LANGUAGES:
            candidates.append(target)
    candidates = candidates[:max_entries]

    def index(file_path: Path) -> tuple[Path, list[str] | None]:
        try:
            names = [item["name"] for item in list_classes(str(file_path))]
            names += [item["name"] for item in list_functions(str(file_path))]
            return file_path, names
        except Exception:
            return file_path, None

    symbols: list[str] = []
    with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 4)) as pool:
        futures = [pool.submit(index, file_path) for file_path in candidates]
        for future in as_completed(futures):
            file_path, names = future.result()
            if names:
                symbols.append(f"{rel(root, file_path)}: {', '.join(names)}")

    supported = ", ".join(sorted(LANGUAGES))
    return {
        "symbols": sorted(symbols),
        "count": len(symbols),
        "note": None if symbols else f"No parseable files. Supported: {supported}",
    }


# ------------------------------------------------------------------- helpers

def _escape(value: str) -> str:
    return "".join(f"\\{char}" if char in r".^$*+?()[]{}|\\" else char for char in value)


def _definitions(root: Path, files: list[str], query: str) -> list[dict]:
    """Mark which hits are real declarations, using tree-sitter."""
    definitions: list[dict] = []
    for name in files[:40]:
        absolute = root / name
        if absolute.suffix.lower() not in LANGUAGES or not absolute.is_file():
            continue
        try:
            for symbol in find_symbols_in_file(str(absolute), query):
                if symbol["name"] == query:
                    definitions.append({
                        "file": name, "line": symbol["line"],
                        "end_line": symbol["end_line"], "type": symbol["type"],
                    })
        except Exception:
            continue
    return definitions
