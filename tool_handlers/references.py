import json
import os
import re
import subprocess

from fastmcp import FastMCP

from config import get_workspace
from .dependencies import iter_files, ripgrep_excludes
from .schemas import SEARCH_OUTPUT_SCHEMA


def _fallback_search(root, text, case_sensitive, max_results, exclude_deps):
    results = []
    needle = text if case_sensitive else text.lower()
    for file_path in iter_files(root, exclude_deps):
        try:
            with open(file_path, encoding="utf-8") as source:
                for line_number, line in enumerate(source, start=1):
                    if needle in (line if case_sensitive else line.lower()):
                        results.append({"file": str(file_path), "line": line_number, "text": line.strip()[:200]})
                        if len(results) >= max_results:
                            return results
        except Exception:
            continue
    return results


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=SEARCH_OUTPUT_SCHEMA)
    def find_references(
        workspace: str,
        symbol_name: str,
        case_sensitive: bool = True,
        max_results: int = 200,
        exclude_deps: bool = True,
    ):
        """Find word-boundary occurrences of a symbol across a workspace."""
        print(f"[tool] find_references(workspace={workspace!r}, symbol_name={symbol_name!r})")
        root = get_workspace(workspace)
        pattern = r"\b" + re.escape(symbol_name) + r"\b"
        command = [
            "rg", "--json", "--line-number", "--hidden", "--follow", "-uu",
        ]
        if exclude_deps:
            command.extend(ripgrep_excludes())
        command.extend((pattern, str(root)))
        if not case_sensitive:
            command.insert(1, "--ignore-case")

        try:
            process = subprocess.run(
                command, capture_output=True, encoding="utf-8", errors="replace", timeout=180,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {"results": _fallback_search(root, symbol_name, case_sensitive, max_results, exclude_deps)}
        if process.returncode not in (0, 1):
            return {"results": _fallback_search(root, symbol_name, case_sensitive, max_results, exclude_deps)}

        results = []
        for line in process.stdout.splitlines():
            try:
                event = json.loads(line)
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
