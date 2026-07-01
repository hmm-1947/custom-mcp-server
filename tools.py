from fastmcp import FastMCP
from pathlib import Path
import os
from terminal.manager import manager
from code_engine.replace import replace_function as ts_replace_function
from config import get_workspace, set_workspace, resolve_path

def register_tools(mcp: FastMCP):

    @mcp.tool()
    def workspace(path: str) -> str:
        """Set the current workspace."""

        set_workspace(path)

        return f"Workspace set to {path}"
    
    @mcp.tool()
    def run_project() -> dict:
        """Run the current project and wait until it finishes."""

        from config import get_run_command

        pid = manager.run(
            get_run_command(),
            cwd=get_workspace()
        )

        return manager.wait(pid)
    
    @mcp.tool()
    def set_run_command(command: str) -> str:
        """Set the project's run command."""

        from config import set_run_command

        set_run_command(command)

        return f"Run command set to: {command}"

    @mcp.tool()
    def set_run_command(command: str) -> str:
        """Set the command used to run the project."""

        from config import set_run_command

        set_run_command(command)

        return f"Run command set to: {command}"
    
    @mcp.tool()
    def wait_for_process(process_id: int) -> dict:
        """Wait until a process finishes and return its output."""

        return manager.wait(process_id)
    
    @mcp.tool()
    def run_command(command: str) -> int:
        """Run a terminal command in the current workspace."""

        return manager.run(
            command,
            cwd=get_workspace()
        )
    
    @mcp.tool()
    def replace_function(
        path: str,
        function_name: str,
        new_function: str,
    ) -> str:
        """Replace an entire function using Tree-sitter."""

        return ts_replace_function(
            str(resolve_path(path)),
            function_name,
            new_function,
        )
    

    @mcp.tool()
    def project_tree() -> str:
        """Return the directory tree of the workspace."""

        root = get_workspace()

        lines = []

        def walk(folder, indent=""):
            items = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))

            for item in items:
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
    def list_directory(path: str = ".") -> list[str]:
        """List files and folders inside a directory."""

        p = resolve_path(path)

        if not p.exists():
            raise FileNotFoundError(f"{path} does not exist")

        if not p.is_dir():
            raise NotADirectoryError(f"{path} is not a directory")

        return sorted(item.name for item in p.iterdir())
    
    @mcp.tool()
    def terminal(process_id: int) -> dict:
        """Read the current terminal output."""

        return manager.get(process_id).output()
    
    @mcp.tool()
    def tail_terminal(process_id: int, lines: int = 20) -> dict:
        """Return the last lines of terminal output."""

        process = manager.get(process_id)

        return {
            "running": process.process.poll() is None,
            "exit_code": process.process.poll(),
            "stdout": process.tail_stdout(lines),
            "stderr": process.tail_stderr(lines),
        }
    
    @mcp.tool()
    def read_file(path: str) -> str:
        """Read a file from the current workspace."""

        p = resolve_path(path)

        if not p.exists():
            raise FileNotFoundError(path)

        if not p.is_file():
            raise IsADirectoryError(path)

        try:
            return p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ValueError("File is not a UTF-8 text file")
        
    @mcp.tool()
    def search_text(
    text: str,
    case_sensitive: bool = False,
    ) -> list[dict]:
        """Search for text in all files under a directory."""

        results = []

        if not case_sensitive:
            text = text.lower()

        for dirpath, _, filenames in os.walk(get_workspace()):
            for filename in filenames:
                path = os.path.join(dirpath, filename)

                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for line_number, line in enumerate(f, start=1):
                            compare = line if case_sensitive else line.lower()

                            if text in compare:
                                results.append({
                                    "file": path,
                                    "line": line_number,
                                    "text": line.strip()
                                })

                except Exception:
                    pass

        return results
    
    @mcp.tool()
    def read_multiple_files(paths: list[str]) -> dict[str, str]:
        """Read multiple text files."""

        result = {}

        for path in paths:
            p = resolve_path(path)

            if not p.exists():
                result[path] = "ERROR: File does not exist"
                continue

            if not p.is_file():
                result[path] = "ERROR: Not a file"
                continue

            try:
                result[path] = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                result[path] = "ERROR: File is not UTF-8 text"
            except Exception as e:
                result[path] = f"ERROR: {e}"

        return result
    
    @mcp.tool()
    def create_file(path: str, content: str = "") -> str:
        """Create a new file in the workspace."""

        p = resolve_path(path)

        if p.exists():
            raise FileExistsError(path)

        p.parent.mkdir(parents=True, exist_ok=True)

        p.write_text(content, encoding="utf-8")

        return f"Created {path}"
    
    @mcp.tool()
    def replace_text(
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = True,
    ) -> str:
        """Replace text inside a file."""

        p = resolve_path(path)

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