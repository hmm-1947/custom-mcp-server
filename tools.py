from fastmcp import FastMCP
import os
import subprocess
from terminal.manager import manager
from code_engine.replace import replace_function as ts_replace_function
from code_engine.finder import (
    list_functions,
    read_function,
    list_classes,
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
    get_run_command,
    set_run_command as config_set_run_command,
    iter_workspace_files,
)
from typing import TypedDict, Optional


class TerminalOutput(TypedDict):
    running: bool
    exit_code: Optional[int]
    stdout: str
    stderr: str
    stdout_total_lines: int
    stderr_total_lines: int


class TerminalInfo(TypedDict):
    name: str
    running: bool


class StopResult(TypedDict):
    success: bool
    exit_code: Optional[int]


class SymbolMatch(TypedDict):
    name: str
    type: str
    line: int
    file: str


class SymbolInfo(TypedDict):
    name: str
    signature: str
    line: int


class SearchMatch(TypedDict):
    file: str
    line: int
    text: str

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
    @mcp.tool()
    def find_symbol(
        workspace: str,
        query: str,
        max_results: int = 50,
    ) -> list[SymbolMatch]:
        """Search all files in the workspace for functions/classes whose name contains query. Returns file, name, type, and line number."""

        results = []
        root = get_workspace(workspace)

        for path in iter_workspace_files(workspace):
            if path.suffix.lower() not in LANGUAGES:
                continue

            try:
                matches = find_symbols_in_file(str(path), query)
            except Exception:
                continue

            for m in matches:
                m["file"] = str(path.relative_to(root))
                results.append(m)

                if len(results) >= max_results:
                    return results

        return results
    

    @mcp.tool()
    def list_classes_tool(
        workspace: str,
        path: str,
    ) -> list[SymbolInfo]:
        """List all classes in a file with name, signature, and line number."""

        return list_classes(
            str(resolve_path(workspace, path))
        )

    @mcp.tool()
    def read_class_tool(
        workspace: str,
        path: str,
        class_name: str,
    ) -> str:
        """Read a class from a file."""

        return read_class(
            str(resolve_path(workspace, path)),
            class_name,
        )


    @mcp.tool()
    def list_functions_tool(
        workspace: str,
        path: str,
    ) -> list[SymbolInfo]:
        """List all functions in a file with name, signature, and line number."""

        return list_functions(
            str(resolve_path(workspace, path))
        )
    

    @mcp.tool()
    def read_function_tool(
        workspace: str,
        path: str,
        function_name: str,
    ) -> str:
        """Read a function from a file."""

        return read_function(
            str(resolve_path(workspace, path)),
            function_name,
        )
    

    @mcp.tool()
    def add_workspace_tool(
        name: str,
        path: str,
    ) -> str:
        """Add a workspace."""

        add_workspace(name, path)

        return f"Workspace '{name}' added"
    
    @mcp.tool()
    def list_workspaces_tool() -> dict[str, str]:
        """List configured workspaces."""

        return list_workspaces()
    
    @mcp.tool()
    def remove_workspace_tool(name: str) -> str:
        """Remove a workspace."""

        remove_workspace(name)

        return f"Workspace '{name}' removed"    
    
    @mcp.tool()
    def run_project(
        workspace: str,
        terminal: str,
        tail: int = 200,
    ) -> TerminalOutput:
        """Run the current project and wait until it finishes. Returns only the last `tail` lines of stdout/stderr by default."""

        manager.run(
            get_run_command(workspace),
            cwd=get_workspace(workspace),
            name=terminal,
        )

        return manager.wait(terminal, tail)

    @mcp.tool()
    def set_run_command(
        workspace: str,
        command: str,
    ) -> str:
        """Set the project's run command."""

        config_set_run_command(workspace, command)

        return f"Run command set to: {command}"

    @mcp.tool()
    def wait_for_terminal(terminal: str, tail: int = 200) -> TerminalOutput:
        """Wait until a terminal finishes. Returns only the last `tail` lines of stdout/stderr by default."""

        return manager.wait(terminal, tail)
        
    @mcp.tool()
    def list_terminals() -> list[TerminalInfo]:
        """List all terminals."""

        return manager.list()
    
    @mcp.tool()
    def run_command(
        workspace: str,
        terminal: str,
        command: str,
    ) -> dict[str, str]:
        """Run a terminal command in the current workspace."""

        manager.run(
            command,
            cwd=get_workspace(workspace),
            name=terminal,
        )

        return {
            "terminal": terminal,
            "status": "started",
        }
    
    @mcp.tool()
    def replace_function(
        workspace: str,
        path: str,
        function_name: str,
        new_function: str,
    ) -> str:
        """Replace an entire function using Tree-sitter."""

        return ts_replace_function(
            str(resolve_path(workspace, path)),
            function_name,
            new_function,
        )
    

    @mcp.tool()
    def project_tree(workspace: str) -> str:
        """Return the directory tree of the workspace."""

        root = get_workspace(workspace)

        lines = []

        SKIP = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build"}

        def walk(folder, indent=""):
            items = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))

            for item in items:
                if item.name in SKIP:
                    continue
                lines.append(f"{indent}{item.name}")

                if item.is_dir():
                    walk(item, indent + "    ")

        lines.append(root.name)

        walk(root)

        return "\n".join(lines)
    

    @mcp.tool()
    def ping() -> str:
        """Check whether the MCP server is alive."""
        return "pong"
    

    @mcp.tool()
    def list_directory(
    workspace: str,
    path=".",
) -> list[str]:
        """List files and folders inside a directory."""

        p = resolve_path(workspace, path)

        if not p.exists():
            raise FileNotFoundError(f"{path} does not exist")

        if not p.is_dir():
            raise NotADirectoryError(f"{path} is not a directory")

        return sorted(item.name for item in p.iterdir())
    
    @mcp.tool()
    def terminal(terminal: str, tail: int = 200) -> TerminalOutput:
        """Get a snapshot of terminal output (last `tail` lines by default, tail=0 for full history). Use this for a one-off check. For repeatedly polling a running process, use tail_terminal instead to avoid re-reading the same lines."""

        return manager.get(terminal).output(tail or None)


    @mcp.tool()
    def stop_terminal(terminal: str) -> StopResult:
        """Stop a running terminal."""

        return manager.stop(terminal)
    

    @mcp.tool()
    def start_project(
        workspace: str,
        terminal: str,
    ) -> dict[str, str]:
        """Start the project without waiting for it to exit."""

        manager.run(
            get_run_command(workspace),
            cwd=get_workspace(workspace),
            name=terminal,
        )

        return {
            "terminal": terminal,
            "status": "started",
        }

    @mcp.tool()
    def tail_terminal(
        terminal: str,
        stdout_cursor: int = 0,
        stderr_cursor: int = 0,
    ) -> dict:
        """Return only new terminal output since the given cursors. Preferred over `terminal` when repeatedly polling a running process, since it avoids re-sending lines already seen. Pass the stdout_cursor/stderr_cursor from the previous response to get only what's new."""

        return manager.tail(
            terminal,
            stdout_cursor,
            stderr_cursor,
        )
    
    @mcp.tool()
    def read_file(
        workspace: str,
        path: str,
    ) -> str:
        """Read a file from the current workspace."""

        p = resolve_path(workspace, path)

        if not p.exists():
            raise FileNotFoundError(path)

        if not p.is_file():
            raise IsADirectoryError(path)

        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ValueError("File is not a UTF-8 text file")

        MAX_CHARS = 20000
        if len(text) > MAX_CHARS:
            return text[:MAX_CHARS] + f"\n... [truncated, {len(text)-MAX_CHARS} more chars]"
        return text
        
    @mcp.tool()
    def search_text(
    workspace: str,
    text: str,
    case_sensitive: bool = False,
    max_results: int = 100,
    ) -> list[SearchMatch]:
        """Search for text in all files under a directory using ripgrep. Respects .gitignore."""

        root = get_workspace(workspace)

        cmd = [
            "rg",
            "--line-number",
            "--no-heading",
            "--max-count", str(max_results),
            text,
            str(root),
        ]

        if not case_sensitive:
            cmd.insert(1, "--ignore-case")

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            return _search_text_fallback(root, text, case_sensitive, max_results)

        if proc.returncode not in (0, 1):
            return _search_text_fallback(root, text, case_sensitive, max_results)

        results = []

        for line in proc.stdout.splitlines():
            parts = line.split(":", 2)

            if len(parts) != 3:
                continue

            file, line_number, content = parts

            results.append({
                "file": file,
                "line": int(line_number),
                "text": content.strip()[:200],
            })

            if len(results) >= max_results:
                break

        return results
    
    @mcp.tool()
    def read_multiple_files(
    workspace: str,
    paths: list[str],
    max_chars_per_file: int = 5000,
)-> dict[str, str]:
        """Read multiple text files. Each file is truncated to max_chars_per_file by default."""

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

                if len(text) > max_chars_per_file:
                    text = text[:max_chars_per_file] + f"\n... [truncated, {len(text) - max_chars_per_file} more chars]"

                result[path] = text
            except UnicodeDecodeError:
                result[path] = "ERROR: File is not UTF-8 text"
            except Exception as e:
                result[path] = f"ERROR: {e}"

        return result
    
    @mcp.tool()
    def create_file(
    workspace: str,
    path: str,
    content: str = "",
) -> str:
        """Create a new file in the workspace."""

        p = resolve_path(workspace, path)

        if p.exists():
            raise FileExistsError(path)

        p.parent.mkdir(parents=True, exist_ok=True)

        p.write_text(content, encoding="utf-8")

        return f"Created {path}"
    
    @mcp.tool()
    def replace_text(
        workspace: str,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = True,
    ) -> str:
        """Replace text inside a file."""

        p = resolve_path(workspace, path)

        if not p.exists():
            raise FileNotFoundError(path)

        text = p.read_text(encoding="utf-8")

        count = text.count(old_text)

        if count == 0:
            return "Text not found"

        if replace_all:
            text = text.replace(old_text, new_text)
        else:
            text = text.replace(old_text, new_text, 1)

        p.write_text(text, encoding="utf-8")

        return f"Replaced {count if replace_all else 1} occurrence(s)"