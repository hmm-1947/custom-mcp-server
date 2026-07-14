from fastmcp import FastMCP
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from code_engine.replace import (
    replace_function as ts_replace_function,
    replace_lines as ts_replace_lines,
    fuzzy_find_text,
)
from pydantic import BaseModel, ConfigDict
from typing import TypedDict, NotRequired

from code_engine.finder import (
    read_function,
    read_class,
    find_symbols_in_file,
)
from code_engine.languages import LANGUAGES
from config import (
    get_workspace,
    add_workspace,
    remove_workspace,
    list_workspaces,
    resolve_path,
    iter_workspace_files,
    SKIP_DIRS,
)


class SearchResultModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    file: str
    line: int
    end_line: int | None = None
    type: str | None = None
    name: str | None = None
    signature: str | None = None
    body: str | None = None
    text: str | None = None


SEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": SearchResultModel.model_json_schema(),
        }
    },
    "required": ["results"],
}

READ_FILES_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": {
        "type": "string",
    },
}

WORKSPACE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "workspaces": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
    },
}

PROJECT_TREE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "tree": {"type": "string"},
    },
    "required": ["tree"],
}

EDIT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
    },
    "required": ["message"],
}

def _search_text_fallback(root, text, case_sensitive, max_results):
    results = []

    compare_text = text if case_sensitive else text.lower()

    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            path = os.path.join(dirpath, filename)

            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line_number, line in enumerate(f, start=1):
                        compare = line if case_sensitive else line.lower()

                        if compare_text in compare:
                            results.append({
                                "file": path,
                                "line": line_number,
                                "text": line.strip()[:200]
                            })

                            if len(results) >= max_results:
                                return results

            except Exception:
                pass

    return results

def register_tools(mcp: FastMCP):
    @mcp.tool(
        output_schema=SEARCH_OUTPUT_SCHEMA,
    )
    def search(
        workspace: str,
        query: str,
        mode: str = "auto",
        include_body: bool = False,
        snippet_lines: int = 0,
        case_sensitive: bool = False,
        max_results: int = 100,
    ):
        """
        Find symbols or text.

        Use to locate code before reading or editing.

        mode:
        - auto
        - symbol
        - text

        include_body:
        Return full function/class source. Prefer snippet_lines instead
        when you only need to confirm a match, not the whole body.

        snippet_lines:
        If > 0 and include_body is False, return only this many lines
        from the start of the function/class body instead of the full
        source - use this to preview a match cheaply.
        """

        print(f"[tool] search(workspace={workspace!r}, query={query!r}, mode={mode!r})")

        if mode not in ("auto", "symbol", "text"):
            raise ValueError("mode must be auto, symbol or text")

        root = get_workspace(workspace)

        if mode in ("auto", "symbol"):
            symbol_results = []

            candidate_paths = [
                path for path in iter_workspace_files(workspace)
                if path.suffix.lower() in LANGUAGES
            ]

            def _scan(path):
                try:
                    return path, find_symbols_in_file(str(path), query)
                except Exception:
                    return path, []

            # Parsing is CPU-bound but tree-sitter's C parser releases the
            # GIL, so scanning files concurrently gives a real speedup on
            # larger workspaces instead of parsing thousands of files
            # one-by-one on a single thread.
            with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 4)) as pool:
                futures = {pool.submit(_scan, path): path for path in candidate_paths}

                for future in as_completed(futures):
                    path, matches = future.result()

                    for m in matches:
                        rel_file = str(path.relative_to(root))

                        try:
                            body = (
                                read_function(str(path), m["name"])
                                if m["type"] == "function"
                                else read_class(str(path), m["name"])
                            )
                        except Exception:
                            continue

                        result = {
                            "name": m["name"],
                            "type": m["type"],
                            "line": m["line"],
                            "end_line": m.get("end_line"),
                            "file": rel_file,
                            "signature": body.splitlines()[0],
                        }

                        if include_body:
                            result["body"] = body
                        elif snippet_lines > 0:
                            body_lines = body.splitlines()
                            result["body"] = "\n".join(body_lines[:snippet_lines])
                            if len(body_lines) > snippet_lines:
                                result["body"] += f"\n... [{len(body_lines) - snippet_lines} more lines]"

                        symbol_results.append(result)

                    if len(symbol_results) >= max_results:
                        for f in futures:
                            f.cancel()
                        break

            symbol_results = symbol_results[:max_results]

            if mode == "symbol" or symbol_results:
                return {"results": symbol_results}

        cmd = [
            "rg",
            "--json",
            "--line-number",
            "--hidden",
            "--follow",
            "-uu",
            "-g", "!node_modules/**",
            "-g", "!venv/**",
            "-g", "!.venv/**",
            "-g", "!env/**",
            "-g", "!.git/**",
            "-g", "!__pycache__/**",
            "-g", "!dist/**",
            "-g", "!build/**",
            "-g", "!.dart_tool/**",
            "-g", "!.pub-cache/**",
            "-g", "!.idea/**",
            "-g", "!.vscode/**",
            "-g", "!target/**",
            "-g", "!.mypy_cache/**",
            "-g", "!.pytest_cache/**",
            "-g", "!**/site-packages/**",
            query,
            str(root),
        ]

        if not case_sensitive:
            cmd.insert(1, "--ignore-case")

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {
                "results": _search_text_fallback(
                    root,
                    query,
                    case_sensitive,
                    max_results,
                )
            }

        if proc.returncode not in (0, 1):
            return {"results": _search_text_fallback(root, query, case_sensitive, max_results)}

        import json

        text_results = []

        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except Exception:
                continue

            if event.get("type") != "match":
                continue

            data = event["data"]

            text_results.append({
                "file": os.path.relpath(data["path"]["text"], root),
                "line": data["line_number"],
                "text": data["lines"]["text"].rstrip()[:200],
            })

            if len(text_results) >= max_results:
                break

        return {"results": text_results}
    
    @mcp.tool(
        output_schema=WORKSPACE_OUTPUT_SCHEMA,
    )
    def workspace(
        action: str,
        name: str = "",
        path: str = "",
    ):
        """
        Manage workspaces.

        Actions:
        - list
        - add
        - remove
        """

        print(f"[tool] workspace(action={action!r}, name={name!r})")

        action = action.lower()

        if action == "list":
            return {"workspaces": list_workspaces()}

        if action == "add":
            if not name:
                raise ValueError("name is required")
            if not path:
                raise ValueError("path is required")

            add_workspace(name, path)
            return {"message": f"Workspace '{name}' added"}

        if action == "remove":
            if not name:
                raise ValueError("name is required")

            remove_workspace(name)
            return {"message": f"Workspace '{name}' removed"}

        raise ValueError("action must be one of: list, add, remove")


    @mcp.tool(
        output_schema=EDIT_OUTPUT_SCHEMA,
    )
    def edit(
        workspace: str,
        path: str,
        function_name: str | None = None,
        new_function: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        replace_all: bool = True,
        create_if_missing: bool = False,
        content: str = "",
        description: str | None = None,
    ) -> str:
        """
        Edit or create files.

        Replace a function, a line range, or exact text - only the
        targeted region is touched, everything else stays untouched.
        Set create_if_missing=True to create a new file.

        Prefer the smallest targeted edit that does the job:
        - old_text + new_text for a specific snippet (exact match, with a
          whitespace-tolerant fallback if the exact text isn't found)
        - start_line + end_line + new_text to replace an exact line range
        - function_name + new_function to replace a whole function/method
        """

        kind = (
            "function_replace" if function_name
            else "line_replace" if start_line is not None
            else "text_replace" if old_text
            else "create/no-op"
        )
        print(f"[tool] edit(workspace={workspace!r}, path={path!r}, kind={kind})")

        p = resolve_path(workspace, path)
        if not p.exists():
            if not create_if_missing:
                raise FileNotFoundError(path)

            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"message": f"Created {path}"}

        if function_name:
            if new_function is None:
                raise ValueError("new_function is required")

            return {
                "message": ts_replace_function(
                    str(p),
                    function_name,
                    new_function,
                )
            }

        if start_line is not None:
            if end_line is None:
                raise ValueError("end_line is required when start_line is set")
            if new_text is None:
                raise ValueError("new_text is required")

            return {
                "message": ts_replace_lines(
                    str(p),
                    start_line,
                    end_line,
                    new_text,
                )
            }

        if old_text:
            if new_text is None:
                raise ValueError("new_text is required")

            text = p.read_text(encoding="utf-8")

            count = text.count(old_text)

            if count == 0:
                found = fuzzy_find_text(text, old_text)

                if found is None:
                    return {"message": "Text not found"}

                start, end = found
                text = text[:start] + new_text + text[end:]
                p.write_text(text, encoding="utf-8")

                return {
                    "message": "Replaced 1 occurrence (fuzzy whitespace match)"
                }

            if replace_all:
                text = text.replace(old_text, new_text)
            else:
                text = text.replace(old_text, new_text, 1)

            p.write_text(text, encoding="utf-8")

            return {
                "message": f"Replaced {count if replace_all else 1} occurrence(s)"
            }

        return {
            "message": (
                "Nothing to edit. "
                "Provide either function_name + new_function, "
                "or old_text + new_text."
            )
        }
    

    @mcp.tool(
        output_schema=PROJECT_TREE_OUTPUT_SCHEMA,
    )
    def project_tree(
        workspace: str,
        path: str = ".",
        max_depth: int = 2,
        max_entries: int = 500,
        show_files: bool = True,
    ) -> str:
        """
        Explore project structure.

        Use before read_files() or edit().

        Args:
        - path: starting folder
        - max_depth: folder depth
        - show_files: include files
        """

        print(f"[tool] project_tree(workspace={workspace!r}, path={path!r})")

        root = resolve_path(workspace, path)

        if not root.exists():
            raise FileNotFoundError(path)

        if not root.is_dir():
            raise NotADirectoryError(path)

        lines = [root.name]
        count = 0

        def walk(folder, depth=0, indent=""):
            nonlocal count

            if depth >= max_depth or count >= max_entries:
                return

            items = sorted(
                folder.iterdir(),
                key=lambda p: (p.is_file(), p.name.lower())
            )

            for item in items:

                if item.name in SKIP_DIRS:
                    continue

                if item.is_file() and not show_files:
                    continue

                lines.append(f"{indent}{item.name}")
                count += 1

                if count >= max_entries:
                    return

                if item.is_dir():
                    walk(
                        item,
                        depth + 1,
                        indent + "    ",
                    )

        walk(root)

        return {"tree": "\n".join(lines)}
    
    @mcp.tool(
        output_schema=READ_FILES_OUTPUT_SCHEMA,
    )
    def read_files(
        workspace: str,
        paths: list[str],
        start_line: int | None = None,
        end_line: int | None = None,
        max_chars_per_file: int = 20000,
    ) -> dict[str, str]:
        """
        Read text files.

        Optionally return only a line range.
        """

        result = {}

        for path in paths:
            p = resolve_path(workspace, path)

            if not p.exists():
                result[path] = "ERROR: File does not exist"
                continue

            if not p.is_file():
                result[path] = "ERROR: Not a file"
                continue

            try:
                text = p.read_text(encoding="utf-8")

                if start_line is not None or end_line is not None:
                    lines = text.splitlines()

                    start = 1 if start_line is None else max(1, start_line)
                    end = len(lines) if end_line is None else min(len(lines), end_line)

                    text = "\n".join(lines[start - 1:end])

                elif len(text) > max_chars_per_file:
                    text = (
                        text[:max_chars_per_file]
                        + f"\n... [truncated, {len(text) - max_chars_per_file} more chars]"
                    )

                result[path] = text

            except UnicodeDecodeError:
                result[path] = "ERROR: File is not UTF-8 text"

            except Exception as e:
                result[path] = f"ERROR: {e}"

        return result