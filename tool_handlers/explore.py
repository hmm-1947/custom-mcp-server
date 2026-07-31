"""Structural exploration: layout and symbol inventory.

Used at the start of a task to build a mental map of the repository cheaply,
before spending reads on individual files.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastmcp import FastMCP

from code_engine.finder import list_classes, list_functions
from code_engine.languages import LANGUAGES
from config import get_workspace, resolve_path
from harness import ripgrep
from ._common import context, relative, respond, tool_schema

EXPLORE_SCHEMA = tool_schema({
    "action": {"type": "string"},
    "root": {"type": "string"},
    "tree": {"type": "string"},
    "files": {"type": "array", "items": {"type": "string"}},
    "count": {"type": "integer"},
    "symbols": {"type": ["object", "array"]},
})

ACTIONS = ("tree", "files", "symbols")


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=EXPLORE_SCHEMA)
    def explore(
        workspace: str,
        action: str = "tree",
        path: str = ".",
        paths: list[str] | None = None,
        max_depth: int = 2,
        max_entries: int = 2000,
        show_files: bool = True,
        detailed: bool = False,
        exclude_deps: bool = True,
    ) -> dict:
        """Map the repository: 'tree' for layout, 'files' for paths, 'symbols' for
        the function/class inventory of a file or directory.

        This is orientation, not a substitute for search. Once you know the shape,
        use find_references to locate the specific code the task touches.
        """
        session, root_path = context(workspace)
        action = (action or "tree").strip().lower()
        if action not in ACTIONS:
            raise ValueError(f"action must be one of: {', '.join(ACTIONS)}")

        root = resolve_path(workspace, path)
        if not root.exists():
            raise FileNotFoundError(path)

        print(f"[harness] explore(action={action!r}, path={path!r})")

        if action == "files":
            if not root.is_dir():
                raise NotADirectoryError(path)
            files: list[str] = []
            for file_path in ripgrep.iter_files(root, exclude_deps):
                try:
                    files.append(str(file_path.relative_to(root)))
                except ValueError:
                    continue
                if len(files) >= max_entries:
                    break
            session.record_search("explore:files", path, len(files), [])
            return respond(session, "explore", {
                "action": action, "root": str(root), "files": files, "count": len(files),
            }, hints=["find_references(symbol_name=...) to locate the code that matters."])

        if action == "symbols":
            payload = _symbols(workspace, root, paths, max_entries, detailed, exclude_deps)
            session.record_search("explore:symbols", path, payload.get("count", 0), [])
            return respond(session, "explore", {"action": action, **payload}, hints=[
                "read_files(paths=[...], start_line=, end_line=) on the symbol you need to change.",
            ])

        if not root.is_dir():
            raise NotADirectoryError(path)

        lines = [root.name]
        count = 0

        def walk(folder, depth=0, indent="    "):
            nonlocal count
            if depth >= max_depth or count >= max_entries:
                return
            try:
                entries = sorted(folder.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
            except OSError:
                return
            for item in entries:
                if exclude_deps and item.name in ripgrep.DEPENDENCY_DIRS:
                    continue
                if item.is_file() and not show_files:
                    continue
                lines.append(f"{indent * (depth + 1)}{item.name}{'/' if item.is_dir() else ''}")
                count += 1
                if count >= max_entries:
                    return
                if item.is_dir():
                    walk(item, depth + 1, indent)

        walk(root)
        session.record_search("explore:tree", path, count, [])
        return respond(session, "explore", {
            "action": action, "root": str(root), "tree": "\n".join(lines), "count": count,
        }, hints=[
            "task(action='orient') if you have not yet detected the toolchain and git state.",
            "find_references(symbol_name=...) to locate the code the goal refers to.",
        ])


def _symbols(workspace, root, paths, max_entries, detailed, exclude_deps) -> dict:
    if paths is not None:
        candidates = [resolve_path(workspace, item) for item in paths]
    elif root.is_file():
        candidates = [root]
    else:
        candidates = [
            file_path for file_path in ripgrep.iter_files(root, exclude_deps)
            if file_path.suffix.lower() in LANGUAGES
        ][:max_entries]

    candidates = [
        file_path for file_path in candidates
        if file_path.is_file() and file_path.suffix.lower() in LANGUAGES
    ][:max_entries]
    workspace_root = get_workspace(workspace)

    def index(file_path):
        return {
            "functions": list_functions(str(file_path)),
            "classes": list_classes(str(file_path)),
        }

    if detailed:
        result: dict[str, object] = {}
        for file_path in candidates:
            key = relative(workspace_root, file_path)
            try:
                result[key] = index(file_path)
            except Exception as error:
                result[key] = f"ERROR: {error}"
        return {"symbols": result, "count": len(result)}

    def compact(file_path):
        try:
            symbols = index(file_path)
            names = [item["name"] for item in symbols["classes"]]
            names.extend(item["name"] for item in symbols["functions"])
            return file_path, names
        except Exception:
            return file_path, None

    lines: list[str] = []
    with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 4)) as pool:
        futures = [pool.submit(compact, file_path) for file_path in candidates]
        for future in as_completed(futures):
            file_path, names = future.result()
            if names:
                lines.append(f"{relative(workspace_root, file_path)}: {', '.join(names)}")
    return {"symbols": sorted(lines), "count": len(lines)}
