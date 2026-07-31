"""Editing, under gates.

The harness makes three refusals here, because these are the three ways an
automated editor damages a repository:

  1. editing a file it has not read            -> guessing at content
  2. editing a file that changed since the read -> acting on stale line numbers
  3. rewriting a whole existing file            -> destroying unrelated work
     and formatting

Everything else - line endings, indentation, trailing newline, bracket balance -
is preserved or checked automatically, and every applied edit moves the task into
the VALIDATE phase so the change cannot quietly go unproven.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from code_engine.finder import read_function
from code_engine.replace import build_function_replacement, fuzzy_find_text
from config import resolve_path
from harness import structure
from harness.session import digest
from ._common import blocked, context, line_window, relative, respond, tool_schema

EDIT_SCHEMA = tool_schema({
    "message": {"type": "string"},
    "path": {"type": "string"},
    "kind": {"type": "string"},
    "applied": {"type": "boolean"},
    "affected_lines": {"type": "object", "additionalProperties": True},
    "context": {"type": "string"},
    "validation": {"type": "object", "additionalProperties": True},
    "formatting": {"type": "array", "items": {"type": "string"}},
    "matches": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    "match_count": {"type": "integer"},
    "warning": {"type": "string"},
    "dry_run": {"type": "boolean"},
    "blocked": {"type": "boolean"},
    "reason": {"type": "string"},
    "required_actions": {"type": "array", "items": {"type": "string"}},
})

FULL_REWRITE_MIN_LINES = 8


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=EDIT_SCHEMA)
    def edit(
        workspace: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        new_text: str | None = None,
        old_text: str | None = None,
        function_name: str | None = None,
        new_function: str | None = None,
        content: str = "",
        create_if_missing: bool = False,
        allow_full_rewrite: bool = False,
        replace_all: bool = False,
        expected_start_text: str | None = None,
        expected_end_text: str | None = None,
        preview_matches: bool = False,
        dry_run: bool = False,
        context_lines: int = 12,
        validate_structure: bool | None = None,
        description: str = "",
    ) -> dict:
        """Make a localized edit. Read the file first - this is enforced.

        Preferred form, cheapest and least ambiguous:
            edit(start_line=, end_line=, new_text=<just the replacement>)
        using the line numbers from read_files. You never reproduce the old code.

        Alternative when you have no reliable line numbers:
            edit(old_text=, new_text=)
        Exact match, with a whitespace-tolerant fallback. replace_all defaults to
        False so a snippet that recurs cannot be rewritten everywhere by accident -
        use preview_matches=True to see every occurrence first.

        Other forms:
            function_name= / new_function=   replace a whole function via tree-sitter
            content= / create_if_missing=True  create a new file

        Refused when: the file was never read, the file changed on disk after you
        read it, or the change would overwrite an entire existing file without
        allow_full_rewrite=True. Line endings, indentation style and the trailing
        newline are preserved, brackets are checked, and the task moves to
        VALIDATE so the edit gets proven.
        """
        session, root = context(workspace)
        root = Path(root)
        target = resolve_path(workspace, path)
        key = relative(root, target)
        exists = target.is_file()
        record = session.files.get(key)

        kind = (
            "function_replace" if function_name else
            "line_replace" if start_line is not None else
            "text_replace" if old_text else
            "create" if (content or not exists) else "none"
        )
        print(f"[harness] edit(path={key!r}, kind={kind}, dry_run={dry_run})")

        if context_lines < 0:
            raise ValueError("context_lines must be zero or greater")

        # ---------------------------------------------------------- new file
        if not exists:
            if not create_if_missing:
                return blocked(
                    session, "edit",
                    f"{key} does not exist.",
                    [
                        f"run(command='ls -la {Path(key).parent.as_posix()}') to confirm the intended path.",
                        "edit(..., create_if_missing=True, content=<full file>) if it really is a new file.",
                    ],
                    path=key,
                )
            body = content or new_text or ""
            if not body:
                return blocked(
                    session, "edit",
                    "Creating a file needs its content.",
                    ["edit(path=..., create_if_missing=True, content=<the whole new file>)"],
                    path=key,
                )
            return _finish(
                session, root, target, key, body, 1, max(1, body.count("\n") + 1),
                f"Created {key}", "create", dry_run, context_lines, validate_structure,
                [], description,
            )

        try:
            # newline="" keeps \r\n intact. Path.read_text applies universal
            # newline translation, which would hide the file's real line-ending
            # convention and silently rewrite a CRLF file as LF on save.
            with target.open("r", encoding="utf-8", newline="") as handle:
                text = handle.read()
        except UnicodeDecodeError:
            return blocked(session, "edit", f"{key} is not UTF-8 text.",
                           ["Inspect it with run() instead of editing it as text."], path=key)

        # ------------------------------------------------------------- gates
        has_read = bool(record and (record.read_full or record.read_ranges))
        if not has_read and not preview_matches and text.strip():
            return blocked(
                session, "edit",
                f"{key} has not been read in this task, so an edit here would be a guess.",
                [
                    f"read_files(paths=['{key}'], start_line=1, end_line=120) - or the region you intend to change.",
                    "find_references(symbol_name=...) first if you do not yet know which region that is.",
                ],
                path=key,
                total_lines=len(text.splitlines()),
            )

        if record and record.read_digest and record.read_digest != digest(target):
            return blocked(
                session, "edit",
                f"{key} changed on disk after you read it; your line numbers and text may be stale.",
                [f"read_files(paths=['{key}'], start_line=..., end_line=...) again, then repeat the edit."],
                path=key,
            )

        total_lines = len(text.splitlines())
        whole_file = bool(content) or (
            start_line == 1 and end_line is not None and end_line >= total_lines
        )
        if whole_file and total_lines >= FULL_REWRITE_MIN_LINES and not allow_full_rewrite:
            return blocked(
                session, "edit",
                (
                    f"This would replace all {total_lines} lines of {key}. Whole-file rewrites lose "
                    "unrelated code and reformat everything around the change."
                ),
                [
                    "Re-target the edit at just the lines that must change "
                    "(edit(start_line=, end_line=, new_text=)).",
                    "Split it into several small edits if more than one region is involved.",
                    "Pass allow_full_rewrite=True only if replacing the entire file really is the task.",
                ],
                path=key,
                total_lines=total_lines,
            )

        # ------------------------------------------------------------ apply
        if function_name:
            if new_function is None:
                raise ValueError("new_function is required with function_name")
            try:
                proposed_bytes, first, last = build_function_replacement(
                    str(target), function_name, new_function,
                )
            except ValueError as error:
                return blocked(
                    session, "edit", str(error),
                    [
                        f"find_references(symbol_name='{function_name}', definitions_only=True) to get its real location.",
                        "Then use edit(start_line=, end_line=, new_text=) instead - it is more reliable.",
                    ],
                    path=key,
                )
            proposed = proposed_bytes.decode("utf-8")
            try:
                previous = read_function(str(target), function_name)
            except Exception:
                previous = ""
            return _finish(session, root, target, key, proposed, first, last,
                           f"Replaced function '{function_name}'", "function_replace", dry_run,
                           context_lines, validate_structure,
                           _formatting_notes(previous, new_function), description)

        if start_line is not None:
            if end_line is None or new_text is None:
                raise ValueError("end_line and new_text are required with start_line")
            lines = text.splitlines()
            if start_line < 1 or end_line < start_line or end_line > len(lines):
                return blocked(
                    session, "edit",
                    f"Invalid line range {start_line}-{end_line} for a file with {len(lines)} lines.",
                    [f"read_files(paths=['{key}']) to get current line numbers."],
                    path=key, total_lines=len(lines),
                )
            if expected_start_text is not None and expected_start_text not in lines[start_line - 1]:
                return blocked(
                    session, "edit",
                    "Start line does not match expected text; the range is stale.",
                    [f"read_files(paths=['{key}'], around_line={start_line}) and retry."],
                    path=key, line=start_line, expected=expected_start_text,
                    actual=lines[start_line - 1],
                )
            if expected_end_text is not None and expected_end_text not in lines[end_line - 1]:
                return blocked(
                    session, "edit",
                    "End line does not match expected text; the range is stale.",
                    [f"read_files(paths=['{key}'], around_line={end_line}) and retry."],
                    path=key, line=end_line, expected=expected_end_text,
                    actual=lines[end_line - 1],
                )

            replaced = "\n".join(lines[start_line - 1:end_line])
            body = _match_line_ending(text, new_text)
            trailing = text.endswith(("\n", "\r\n"))
            source = text.splitlines()
            proposed_lines = source[:start_line - 1] + body.splitlines() + source[end_line:]
            newline = "\r\n" if _dominant_ending(text) == "\r\n" else "\n"
            proposed = newline.join(proposed_lines) + (newline if trailing else "")
            return _finish(
                session, root, target, key, proposed, start_line,
                start_line + max(0, body.count("\n")),
                f"Replaced lines {start_line}-{end_line} of {key}", "line_replace", dry_run,
                context_lines, validate_structure, _formatting_notes(replaced, new_text), description,
            )

        if old_text:
            # Match the caller's snippet against the file's own line endings, so
            # searching a CRLF file with LF-separated text still finds the region.
            old_text = _match_line_ending(text, old_text)
            count = text.count(old_text)

            if preview_matches:
                if count:
                    return respond(session, "edit", {
                        "message": f"Preview: {count} exact match(es); nothing applied.",
                        "path": key, "kind": "preview", "applied": False,
                        "matches": _previews(text, old_text), "match_count": count,
                    }, hints=[
                        "Make old_text longer/unique for the one occurrence you want, "
                        "or use edit(start_line=, end_line=) with the line numbers above."
                        if count > 1 else "Unique match - safe to apply.",
                    ])
                span = fuzzy_find_text(text, old_text)
                if span is None:
                    return respond(session, "edit", {
                        "message": "Preview: text not found.", "path": key, "kind": "preview",
                        "applied": False, "matches": [], "match_count": 0,
                    }, hints=[f"read_files(paths=['{key}']) to see the current text."])
                first = text.count("\n", 0, span[0]) + 1
                last = text.count("\n", 0, span[1]) + 1
                return respond(session, "edit", {
                    "message": "Preview: 1 whitespace-tolerant match; nothing applied.",
                    "path": key, "kind": "preview", "applied": False, "match_count": 1,
                    "matches": [{
                        "index": 1, "start_line": first, "end_line": last,
                        "context": line_window(text, first, last, 3),
                    }],
                })

            if new_text is None:
                raise ValueError("new_text is required with old_text")

            if count == 0:
                span = fuzzy_find_text(text, old_text)
                if span is None:
                    return blocked(
                        session, "edit", "old_text was not found in the file.",
                        [
                            f"read_files(paths=['{key}']) and copy the exact current text, or",
                            "edit(preview_matches=True) with a shorter distinctive fragment.",
                        ],
                        path=key,
                    )
                body = _match_line_ending(text, new_text)
                proposed = text[:span[0]] + body + text[span[1]:]
                offset, message = span[0], "Replaced 1 occurrence (whitespace-tolerant match)"
                warning = None
            elif count > 1 and not replace_all:
                return blocked(
                    session, "edit",
                    f"old_text occurs {count} times in {key}; refusing to guess which one you meant.",
                    [
                        "edit(preview_matches=True, old_text=...) to see all occurrences with line numbers.",
                        "Then either extend old_text so it is unique, use edit(start_line=, end_line=), "
                        "or pass replace_all=True if every occurrence really should change.",
                    ],
                    path=key, match_count=count, matches=_previews(text, old_text),
                )
            else:
                body = _match_line_ending(text, new_text)
                offset = text.index(old_text)
                proposed = text.replace(old_text, body) if replace_all else text.replace(old_text, body, 1)
                message = f"Replaced {count if replace_all else 1} occurrence(s) in {key}"
                warning = (
                    f"all {count} occurrences were replaced" if replace_all and count > 1 else None
                )
            first = text.count("\n", 0, offset) + 1
            notes = _formatting_notes(old_text, new_text)
            return _finish(session, root, target, key, proposed, first,
                           first + max(0, body.count("\n")), message, "text_replace", dry_run,
                           context_lines, validate_structure, notes, description,
                           warning=warning)

        return blocked(
            session, "edit", "Nothing to do: no replacement was specified.",
            [
                "edit(start_line=, end_line=, new_text=) - preferred, after read_files.",
                "edit(old_text=, new_text=) - when you have no reliable line numbers.",
                "edit(create_if_missing=True, content=) - for a brand-new file.",
            ],
            path=key,
        )


# --------------------------------------------------------------------- helpers

def _finish(session, root, target: Path, key: str, proposed: str, first: int, last: int,
            message: str, kind: str, dry_run: bool, context_lines: int,
            validate_structure: bool | None, formatting: list[str], description: str,
            warning: str | None = None) -> dict:
    """Structural check, write, record, and hand back the validation instruction."""
    should_validate = (
        validate_structure if validate_structure is not None
        else target.suffix.lower() in structure.STRUCTURAL_EXTENSIONS
    )
    if should_validate:
        problem = structure.validate_structure(target, proposed)
        if problem is not None:
            return blocked(
                session, "edit",
                "Edit not applied: it would leave brackets unbalanced.",
                [
                    "Re-read the region and include the matching closing bracket in your replacement.",
                    f"read_files(paths=['{key}'], around_line={problem.get('line', first)})",
                ],
                path=key, validation=problem,
            )

    payload = {
        "message": f"Dry run: {message}" if dry_run else message,
        "path": key, "kind": kind, "applied": not dry_run,
        "affected_lines": {"start": first, "end": last},
        "context": line_window(proposed, first, last, context_lines or 12),
    }
    if formatting:
        payload["formatting"] = formatting
    if warning:
        payload["warning"] = warning
    if dry_run:
        payload["dry_run"] = True
        return respond(session, "edit", payload,
                       hints=["Re-run without dry_run=True to apply this."])

    target.parent.mkdir(parents=True, exist_ok=True)
    # newline="" writes exactly the bytes we assembled, so the file keeps the
    # line endings it already had instead of picking up the platform default.
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write(proposed)
    session.record_edit(key, kind, target, (first, last))
    if description:
        session.note(f"edit {key}:{first}-{last} - {description}")

    hints = ["validate() now - an unvalidated edit is not a finished edit."]
    if formatting:
        hints.append("Formatting differences were detected; check the context above before validating.")
    remaining = [
        path for path in session.impact_candidates
        if path not in session.read_files() and path != key
    ]
    if remaining:
        hints.append("Still unexamined from impact analysis: " + ", ".join(remaining[:4]))
    return respond(session, "edit", payload, hints=hints)


def _previews(text: str, old_text: str) -> list[dict]:
    matches: list[dict] = []
    index = 0
    while True:
        found = text.find(old_text, index)
        if found < 0:
            return matches
        first = text.count("\n", 0, found) + 1
        last = first + old_text.count("\n")
        matches.append({
            "index": len(matches) + 1, "start_line": first, "end_line": last,
            "context": line_window(text, first, last, 3),
        })
        index = found + len(old_text)


def _dominant_ending(text: str) -> str:
    crlf = text.count("\r\n")
    return "\r\n" if crlf and crlf >= (text.count("\n") - crlf) else "\n"


def _match_line_ending(existing: str, new_text: str) -> str:
    """Rewrite the incoming text to the file's own line-ending convention."""
    normalized = new_text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", "\r\n") if _dominant_ending(existing) == "\r\n" else normalized


def _indent_of(text: str) -> tuple[str, int] | None:
    for line in text.split("\n"):
        if not line.strip():
            continue
        stripped = line.lstrip(" \t")
        prefix = line[: len(line) - len(stripped)]
        if not prefix:
            return ("none", 0)
        return ("tab", prefix.count("\t")) if "\t" in prefix else ("space", len(prefix))
    return None


def _formatting_notes(old_segment: str, new_segment: str) -> list[str]:
    """Warn about formatting drift rather than silently normalizing the file."""
    notes: list[str] = []
    before = _indent_of(old_segment or "")
    after = _indent_of(new_segment or "")
    if before and after and before[0] != after[0] and "none" not in (before[0], after[0]):
        notes.append(
            f"indentation style changed: the original region used {before[0]}s, the replacement uses {after[0]}s"
        )
    elif before and after and before[0] == after[0] == "space" and before[1] != after[1]:
        notes.append(
            f"leading indentation changed from {before[1]} to {after[1]} spaces - "
            "confirm the new block sits at the right nesting level"
        )
    if (old_segment or "").endswith("\n") and not (new_segment or "").endswith("\n"):
        notes.append("the replaced region ended with a newline and the replacement does not")
    if "\t" in (new_segment or "") and "\t" not in (old_segment or ""):
        notes.append("tabs were introduced into a region that used spaces")
    return notes
