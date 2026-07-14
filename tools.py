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
    find_symbols_in_file_with_bodies,
    list_functions,
    list_classes,
)
from code_engine.read_cache import (
    check_and_mark as _read_cache_check_and_mark,
    check_and_mark_symbols as _read_cache_check_and_mark_symbols,
    get_symbol_index as _read_cache_get_symbol_index,
    set_symbol_index as _read_cache_set_symbol_index,
    invalidate as _read_cache_invalidate,
    prune as _read_cache_prune,
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

LIST_SYMBOLS_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "properties": {
            "functions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "signature": {"type": "string"},
                        "line": {"type": "integer"},
                    },
                },
            },
            "classes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "signature": {"type": "string"},
                        "line": {"type": "integer"},
                    },
                },
            },
        },
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
                    return path, find_symbols_in_file_with_bodies(str(path), query)
                except Exception:
                    return path, []

            # Parsing is CPU-bound but tree-sitter's C parser releases the
            # GIL, so scanning files concurrently gives a real speedup on
            # larger workspaces instead of parsing thousands of files
            # one-by-one on a single thread. Each file is parsed exactly
            # once regardless of how many symbols in it match the query.
            with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 4)) as pool:
                futures = {pool.submit(_scan, path): path for path in candidate_paths}

                for future in as_completed(futures):
                    path, matches = future.result()

                    for m in matches:
                        rel_file = str(path.relative_to(root))
                        body = m["body"]

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
        - prune_cache: drop cached symbol/read-state entries for files
          that no longer exist in this workspace (e.g. after deletes or
          renames outside of edit()). Requires name.
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

        if action == "prune_cache":
            if not name:
                raise ValueError("name is required")

            existing = {str(p) for p in iter_workspace_files(name)}
            removed = _read_cache_prune(existing)

            return {"message": f"Pruned {removed} stale cache entr{'y' if removed == 1 else 'ies'}"}

        raise ValueError("action must be one of: list, add, remove, prune_cache")


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

        Default to old_text + new_text and make it as small as possible -
        just the exact snippet that's actually changing, not the
        surrounding code. Changing one parameter's default value means
        old_text is that one line (or even just the token being changed),
        never the whole function signature or body.

        Example: to change `name: str = ""` to `name: str = "joshua"`
        inside `def workspace(...)`, use:
            old_text="name: str = \\"\\""
            new_text="name: str = \\"joshua\\""
        Do NOT set old_text to the entire `def workspace(...):` block -
        that rewrites lines that never changed and makes the diff harder
        to review.

        Options, in order of preference:
        - old_text + new_text: smallest exact snippet that changed (exact
          match required, with a whitespace-tolerant fallback if it isn't
          found verbatim). If old_text isn't unique in the file, add just
          enough surrounding text to make it unique - still far less than
          the whole function.
        - start_line + end_line + new_text: only when you already know
          the precise line numbers and old_text would be ambiguous or
          awkward to quote.
        - function_name + new_function: last resort. Rewrites the entire
          function body, including every line that didn't change - only
          use this when you're restructuring most of the function, never
          for a single-line, single-value, or single-parameter change.
        """

        kind = (
            "function_replace" if function_name
            else "line_replace" if start_line is not None
            else "text_replace" if old_text
            else "create/no-op"
        )
        print(f"[tool] edit(workspace={workspace!r}, path={path!r}, kind={kind})")

        p = resolve_path(workspace, path)
        _read_cache_invalidate(p)

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

        Use before read_files() or edit(). For a specific file, prefer
        list_symbols() next to see its functions/classes before deciding
        whether to read the whole file.

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

        By default reads the whole file (up to max_chars_per_file). If you
        don't already know which lines you need, call list_symbols() first
        to get exact line numbers for the function/class you're after,
        rather than guessing a range. When you do pass start_line/end_line,
        the response is annotated with line numbers and how many lines
        were left out on each side, since a narrow range without full-file
        context can miss related code above/below it. When in doubt, read
        the full file once and work from the results already in front of
        you rather than re-reading the same file again.
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
                if _read_cache_check_and_mark(p, start_line, end_line):
                    result[path] = (
                        "UNCHANGED: this file (same range) was already read "
                        "in a previous call and has not been modified since. "
                        "Use the content already in context - no need to "
                        "re-read it. If you specifically need to see it "
                        "again, call edit() first to invalidate, or read a "
                        "different line range."
                    )
                    continue

                text = p.read_text(encoding="utf-8")

                if start_line is not None or end_line is not None:
                    lines = text.splitlines()
                    total = len(lines)

                    start = 1 if start_line is None else max(1, start_line)
                    end = total if end_line is None else min(total, end_line)

                    numbered = "\n".join(
                        f"{i:>5} | {lines[i - 1]}"
                        for i in range(start, end + 1)
                    )

                    header = f"[showing lines {start}-{end} of {total} total lines]"

                    before = (
                        f"... [{start - 1} line(s) before this range, not shown]\n"
                        if start > 1 else
                        "[start of file]\n"
                    )
                    after = (
                        f"\n... [{total - end} line(s) after this range, not shown]"
                        if end < total else
                        "\n[end of file]"
                    )

                    text = f"{header}\n{before}{numbered}{after}"

                elif len(text) > max_chars_per_file:
                    text = (
                        text[:max_chars_per_file]
                        + f"\n... [truncated, {len(text) - max_chars_per_file} more chars. "
                        + "Total file has more content - use start_line/end_line to read further, "
                        + "or increase max_chars_per_file]"
                    )

                result[path] = text

            except UnicodeDecodeError:
                result[path] = "ERROR: File is not UTF-8 text"

            except Exception as e:
                result[path] = f"ERROR: {e}"

        return result

    @mcp.tool(
        output_schema=LIST_SYMBOLS_OUTPUT_SCHEMA,
    )
    def list_symbols(
        workspace: str,
        paths: list[str],
    ) -> dict:
        """
        List all functions and classes in one or more files - name,
        signature, and line number only, no bodies.

        Use this before read_files() when you don't yet know which part
        of a file you need. It's far cheaper than reading the whole file,
        and gives you the exact line numbers to pass to read_files() or
        the exact name to pass to search()/edit() once you've picked the
        symbol you actually need.

        If a file's outline hasn't changed since it was last listed, the
        response is a short "UNCHANGED" notice instead of repeating the
        full list - the outline you already have is still accurate.
        """

        print(f"[tool] list_symbols(workspace={workspace!r}, paths={paths!r})")

        result = {}

        for path in paths:
            p = resolve_path(workspace, path)

            if not p.exists():
                result[path] = "ERROR: File does not exist"
                continue

            if not p.is_file():
                result[path] = "ERROR: Not a file"
                continue

            if p.suffix.lower() not in LANGUAGES:
                result[path] = "ERROR: Unsupported file type for symbol listing"
                continue

            try:
                if _read_cache_check_and_mark_symbols(p):
                    result[path] = (
                        "UNCHANGED: the symbol list for this file was already "
                        "shown in a previous call and the file has not been "
                        "modified since. Use the outline already in context."
                    )
                    continue

                cached = _read_cache_get_symbol_index(p)

                if cached is not None:
                    functions, classes = cached
                else:
                    functions = list_functions(str(p))
                    classes = list_classes(str(p))
                    _read_cache_set_symbol_index(p, (functions, classes))

                result[path] = {
                    "functions": functions,
                    "classes": classes,
                }

            except Exception as e:
                result[path] = f"ERROR: {e}"

        return result

    @mcp.tool(
        output_schema=SEARCH_OUTPUT_SCHEMA,
    )
    def find_references(
        workspace: str,
        symbol_name: str,
        case_sensitive: bool = True,
        max_results: int = 200,
    ):
        """
        Find every place a symbol name is used across the workspace - call
        sites, imports, instantiations - not just where it's defined.

        Use this before renaming or changing a function/class signature,
        to see everything that would be affected without reading whole
        files. Returns file, line, and a short line of context per hit -
        no bodies, so it stays cheap even with many references.

        This is a plain text/word-boundary search, not semantic analysis:
        it will not distinguish two different symbols that happen to
        share a name in different scopes. For that, narrow max_results
        or the workspace path, or follow up with read_files() on the
        specific hits that matter.
        """

        print(f"[tool] find_references(workspace={workspace!r}, symbol_name={symbol_name!r})")

        root = get_workspace(workspace)

        import re

        pattern = r"\b" + re.escape(symbol_name) + r"\b"

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
            pattern,
            str(root),
        ]

        if not case_sensitive:
            cmd.insert(1, "--ignore-case")

        import json as _json

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
                    symbol_name,
                    case_sensitive,
                    max_results,
                )
            }

        if proc.returncode not in (0, 1):
            return {"results": _search_text_fallback(root, symbol_name, case_sensitive, max_results)}

        results = []

        for line in proc.stdout.splitlines():
            try:
                event = _json.loads(line)
            except Exception:
                continue

            if event.get("type") != "match":
                continue

            data = event["data"]

            results.append({
                "file": os.path.relpath(data["path"]["text"], root),
                "line": data["line_number"],
                "text": data["lines"]["text"].rstrip()[:200],
            })

            if len(results) >= max_results:
                break

        return {"results": results}

    @mcp.tool(
        output_schema=PROJECT_TREE_OUTPUT_SCHEMA,
    )
    def symbol_map(
        workspace: str,
        path: str = ".",
        max_entries: int = 2000,
    ) -> str:
        """
        Build a compact whole-project map of every function and class:
        `relative/path.py: ClassName, function_name, another_function`.

        Use this once at the start of a task to understand a whole
        project's shape - what's defined and where - in a single call,
        instead of calling list_symbols() file-by-file. Far cheaper than
        reading every file, and cheaper than list_symbols() per-file for
        an initial orientation pass since results are one line per file
        instead of a nested structure with line numbers and signatures.
        Follow up with list_symbols() on a specific file once you know
        which one you need, to get exact line numbers and signatures.

        Results are drawn from the same on-disk symbol cache list_symbols()
        uses, so files already indexed in a previous call are effectively
        free; only new or changed files are re-parsed.
        """

        print(f"[tool] symbol_map(workspace={workspace!r}, path={path!r})")

        root = get_workspace(workspace)
        start = resolve_path(workspace, path)

        if not start.exists():
            raise FileNotFoundError(path)

        if start.is_dir():
            all_paths = start.rglob("*")
        else:
            all_paths = [start]

        candidate_paths = [
            p for p in all_paths
            if p.is_file()
            and p.suffix.lower() in LANGUAGES
            and not any(part in SKIP_DIRS for part in p.relative_to(root).parts)
        ]

        def _index_one(p):
            try:
                cached = _read_cache_get_symbol_index(p)
                if cached is not None:
                    functions, classes = cached
                else:
                    functions = list_functions(str(p))
                    classes = list_classes(str(p))
                    _read_cache_set_symbol_index(p, (functions, classes))

                names = [c["name"] for c in classes] + [f["name"] for f in functions]
                return p, names
            except Exception:
                return p, None

        lines = []

        with ThreadPoolExecutor(max_workers=min(32, (os.cpu_count() or 4) * 4)) as pool:
            futures = [pool.submit(_index_one, p) for p in candidate_paths[:max_entries]]

            for future in as_completed(futures):
                p, names = future.result()

                if not names:
                    continue

                rel = str(p.relative_to(root))
                lines.append(f"{rel}: {', '.join(names)}")

        lines.sort()

        return {"tree": "\n".join(lines)}