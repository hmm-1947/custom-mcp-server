"""Search: the exploration step.

Nothing is edited before it is located. This tool answers "where does this
exist, and in how many roles" - declarations, call sites, imports, route
registrations, schema fields, config keys - and records the hits on the task so
the harness knows exploration actually happened.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from code_engine.finder import find_symbols_in_file
from code_engine.languages import LANGUAGES
from harness import ripgrep
from ._common import context, relative, respond, tool_schema

SEARCH_SCHEMA = tool_schema({
    "query": {"type": "string"},
    "engine": {"type": "string"},
    "count": {"type": "integer"},
    "results": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    "by_file": {"type": "object", "additionalProperties": True},
    "definitions": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    "truncated": {"type": "boolean"},
})

#: Extra things worth locating for a given kind of change. The harness suggests
#: these rather than hardcoding a sequence, because which ones matter depends on
#: the project.
COMPANION_QUERIES = {
    "route": ("router", "app.get", "app.post", "@app.", "urlpatterns", "Navigator"),
    "model": ("Column(", "models.", "@dataclass", "BaseModel", "Entity"),
    "schema": ("Schema", "serializer", "toJson", "fromJson", "pydantic"),
    "migration": ("op.add_column", "alembic", "CREATE TABLE", "ALTER TABLE"),
    "config": ("os.environ", "getenv", "dotenv", "Settings", "config["),
}


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=SEARCH_SCHEMA)
    def find_references(
        workspace: str,
        symbol_name: str,
        case_sensitive: bool = True,
        max_results: int = 200,
        context_lines: int = 2,
        exclude_deps: bool = True,
        regex: bool = False,
        file_glob: str = "",
        definitions_only: bool = False,
    ) -> dict:
        """Locate a symbol everywhere it appears, with surrounding context.

        Use this for every function, class, method, route, model, schema field,
        migration, API path, config key and constant the task touches - before
        reading files and long before editing.

        context_lines defaults to 2 so a declaration, a call site and an import can
        be told apart without a follow-up read. Results are grouped by file, and
        tree-sitter is used to mark which hits are actual definitions.

        Set regex=True to search a pattern instead of a literal symbol; use
        file_glob (e.g. '*.dart', '!*test*') to narrow the sweep.
        """
        session, root = context(workspace)
        root = Path(root)
        query = symbol_name.strip()
        if not query:
            raise ValueError("symbol_name is required")

        pattern = query if regex else rf"\b{_escape(query)}\b"
        print(f"[harness] find_references({query!r}, regex={regex})")

        found = ripgrep.search(
            root, pattern,
            regex=True,
            case_sensitive=case_sensitive,
            max_results=max_results,
            context_lines=max(0, context_lines),
            exclude_deps=exclude_deps,
            globs=[file_glob] if file_glob else None,
        )
        results = found["results"]

        by_file: dict[str, list[dict]] = {}
        for hit in results:
            by_file.setdefault(hit["file"], []).append({
                "line": hit["line"], "text": hit["text"], "context": hit.get("context"),
            })

        definitions = _definitions(root, list(by_file), query) if not regex else []
        definition_files = {item["file"] for item in definitions}
        if definitions_only and definitions:
            results = [hit for hit in results if hit["file"] in definition_files]
            by_file = {name: hits for name, hits in by_file.items() if name in definition_files}

        session.record_search("references", query, len(results), list(by_file))

        hints: list[str] = []
        if not results:
            hints.append(
                f"No hits for {query!r}. Try case_sensitive=False, a shorter fragment, "
                "regex=True, or exclude_deps=False if it may live in a dependency."
            )
        else:
            if definitions:
                hints.append(
                    "Definition(s) at " + ", ".join(f"{item['file']}:{item['line']}" for item in definitions[:3])
                    + ". Read there first, then the call sites."
                )
            else:
                hints.append(
                    f"{len(results)} hit(s) across {len(by_file)} file(s) but no definition found in a parsed "
                    "language - the symbol may be defined dynamically, in config, or in a dependency."
                )
            if len(by_file) > 1:
                hints.append(
                    "Multiple files match. Before any edit whose old_text could recur, "
                    "use edit(preview_matches=True) to confirm you are changing the right occurrence."
                )
            hints.append(f"impact(paths={sorted(definition_files) or list(by_file)[:3]}) to find dependents.")

        return respond(session, "find_references", {
            "query": query,
            "engine": found["engine"],
            "count": len(results),
            "results": results,
            "by_file": by_file,
            "definitions": definitions,
            "truncated": len(results) >= max_results,
            "companion_queries": COMPANION_QUERIES,
        }, hints=hints)


def _escape(value: str) -> str:
    return "".join(f"\\{char}" if char in r".^$*+?()[]{}|\\" else char for char in value)


def _definitions(root: Path, files: list[str], query: str) -> list[dict]:
    """Use tree-sitter to mark which hits are real declarations."""
    definitions: list[dict] = []
    for name in files[:40]:
        absolute = root / name
        if absolute.suffix.lower() not in LANGUAGES or not absolute.is_file():
            continue
        try:
            for symbol in find_symbols_in_file(str(absolute), query):
                if symbol["name"] == query:
                    definitions.append({
                        "file": name, "line": symbol["line"], "end_line": symbol["end_line"],
                        "type": symbol["type"], "name": symbol["name"],
                    })
        except Exception:
            continue
    return definitions
