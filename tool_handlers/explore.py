import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastmcp import FastMCP

from code_engine.finder import list_classes, list_functions
from code_engine.languages import LANGUAGES
from config import SKIP_DIRS, get_workspace, resolve_path
from .dependencies import iter_files
from .schemas import EXPLORE_OUTPUT_SCHEMA


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=EXPLORE_OUTPUT_SCHEMA)
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
        """Explore a workspace with tree, files, or symbols actions."""
        action = action.lower()
        if action not in {"tree", "files", "symbols"}:
            raise ValueError("action must be one of: tree, files, symbols")

        print(f"[tool] explore(workspace={workspace!r}, action={action!r}, path={path!r})")
        root = resolve_path(workspace, path)
        if not root.exists():
            raise FileNotFoundError(path)

        if action == "files":
            if not root.is_dir():
                raise NotADirectoryError(path)
            files = []
            for file_path in iter_files(root, exclude_deps):
                try:
                    files.append(str(file_path.relative_to(root)))
                except ValueError:
                    continue
                if len(files) >= max_entries:
                    break
            return {"root": str(root), "files": files, "count": len(files)}

        if action == "symbols":
            if paths is not None:
                candidates = [resolve_path(workspace, item) for item in paths]
            elif root.is_file():
                candidates = [root]
            else:
                candidates = [
                    file_path for file_path in iter_files(root, exclude_deps)
                    if file_path.suffix.lower() in LANGUAGES and root in file_path.parents
                ][:max_entries]

            candidates = [
                file_path for file_path in candidates
                if file_path.is_file() and file_path.suffix.lower() in LANGUAGES
            ]
            workspace_root = get_workspace(workspace)

            def index(file_path):
                return {
                    "functions": list_functions(str(file_path)),
                    "classes": list_classes(str(file_path)),
                }

            if detailed:
                result = {}
                for file_path in candidates[:max_entries]:
                    try:
                        result[str(file_path.relative_to(workspace_root))] = index(file_path)
                    except Exception as exc:
                        result[str(file_path)] = f"ERROR: {exc}"
                return {"symbols": result}

            def compact(file_path):
                try:
                    symbols = index(file_path)
                    names = [item["name"] for item in symbols["classes"]]
                    names.extend(item["name"] for item in symbols["functions"])
                    return file_path, names
                except Exception:
                    return file_path, None

            lines = []
            with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 4)) as pool:
                futures = [pool.submit(compact, file_path) for file_path in candidates[:max_entries]]
                for future in as_completed(futures):
                    file_path, names = future.result()
                    if names:
                        lines.append(f"{file_path.relative_to(workspace_root)}: {', '.join(names)}")
            return {"symbols": sorted(lines)}

        if not root.is_dir():
            raise NotADirectoryError(path)

        lines = [root.name]
        count = 0

        def walk(folder, depth=0, indent=""):
            nonlocal count
            if depth >= max_depth or count >= max_entries:
                return
            for item in sorted(folder.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
                if (
                    (exclude_deps and item.name in SKIP_DIRS)
                    or (item.is_file() and not show_files)
                ):
                    continue
                lines.append(f"{indent}{item.name}")
                count += 1
                if count >= max_entries:
                    return
                if item.is_dir():
                    walk(item, depth + 1, indent + "    ")

        walk(root)
        return {"tree": "\n".join(lines)}
