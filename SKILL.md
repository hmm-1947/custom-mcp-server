---
name: cody-mcp-tool
description: Use whenever the cody MCP connector (tools cody:explore, cody:read_files, cody:edit, cody:find_references, cody:workspace) is available and the user asks to inspect, search, or edit code in a project registered with Cody. Covers workspace resolution, reliable edit patterns, avoiding stale-cache reads, safe multi-match replacements, and known gotchas (e.g. Alembic NOT NULL columns needing server_default). Applies across any codebase using Cody, not just one specific project.
---

# Cody MCP Tool

Cody is a local MCP server that gives read/search/edit access to codebases registered as "workspaces" (folder aliases). It is project-agnostic — the same server can have many workspaces registered (e.g. a Flutter app, a FastAPI backend, a completely unrelated repo), each addressed by a short alias name instead of a raw path.

This skill is about using the **tool** correctly and efficiently. It has no knowledge of any specific project's code — that lives in per-project memory/notes.

## Before doing anything: resolve workspaces

Call `cody:workspace` with `action: "list"` first if you don't already know the workspace aliases for this session. Never guess a raw path — always use the registered alias.

```
cody:workspace(action="list")
```

If a workspace you need isn't listed, add it:

```
cody:workspace(action="add", name="my_alias", path="/absolute/path")
```

If a previously-working alias fails to resolve (empty result, "workspace not found"), the MCP server has likely lost its config — try `list` again first; if it's genuinely gone, re-add it. Don't assume a restart is needed until `list` confirms the alias is actually missing.

## Investigate before editing — always

Never call `cody:edit` on a symbol/behavior you haven't located first. The standard sequence:

1. `cody:find_references` on the symbol/function/route name to find every place it appears
2. `cody:read_files` with `start_line`/`end_line` around the relevant hits to see real context
3. Only then `cody:edit`

Skipping step 1–2 is how wrong-target edits happen (see "Multi-match replacements" below).

### Use `context_lines` on find_references to cut round-trips

`cody:find_references` accepts `context_lines` (e.g. `context_lines=2`). Passing this returns a few lines of surrounding code with each hit, which is usually enough to tell a declaration apart from a call site or an import without needing a separate `read_files` call. Default to `context_lines=2` any time you expect a symbol to appear in multiple different roles (e.g. a function that's both defined and called multiple times), and always before an edit that will use `replace_all`.

## `cody:edit` — reliable patterns

- **Prefer `old_text`/`new_text` exact-match replacement.** It's the most reliable mode.
- **`function_name`/`new_function` is unreliable** — it can silently fail to match. Don't rely on it as your only edit path; fall back to `old_text`/`new_text` if it doesn't behave as expected.
- **`create_if_missing`/`content` for brand-new files can silently fail** on some setups. If a new-file creation doesn't stick, retry using `start_line: 1`, `end_line: 1`, `new_text: <full file content>` against the (now-existing, even if empty) file instead.
- **Re-view before re-editing the same file.** After any successful edit, prior `read_files` output of that file in your context is stale. If you need to make a second edit nearby, re-read first — don't trust your memory of line numbers or exact text from before the first edit.
- **Stale read cache**: if a file was just changed outside of Cody (e.g. by another tool, or you suspect the read cache is out of date), a no-op `cody:edit` (matching old_text to itself, or a zero-effect line edit) can force cache invalidation before the next read.

### Multi-match replacements — the most common failure mode

`old_text` must be unique, but short/common snippets (e.g. `_audioLevelTimer?.cancel();`, `await deduct_chat_coins(...)`) often appear in more than one place with identical text. `replace_all` (the default) will silently rewrite **every** occurrence, including ones you didn't mean to touch.

Before running an edit whose `old_text` looks like it could recur elsewhere in the file:

1. Run `cody:find_references` (or `cody:edit` with `preview_matches=True`, if supported by the server) to see how many matches exist and where.
2. If there are multiple matches and they're not *all* supposed to change identically, make `old_text` longer/more specific (include a distinguishing neighboring line) so it's unique to the one call site you want.
3. After the edit, always re-run `cody:find_references` on the changed symbol/text to confirm the edit landed only where intended — the tool call is cheap; a silently-wrong edit in production code is not.

If the edit response includes a warning that `old_text` occurred N times and all were replaced, treat that as a signal to verify — don't just move on.

## Known cross-project gotchas worth checking for

These recur across different backends/languages and are cheap to check before finishing a task:

- **Alembic / SQL migrations**: adding a `NOT NULL` column to an existing table needs `server_default=...` in the migration (or a data-backfill step) or `alembic upgrade head` will fail on any table with existing rows. If you write or generate a migration adding a non-nullable column, check for `server_default` before considering the task done.
- **Schema-only vs. DB-column changes**: adding a field to an API response schema (e.g. a Pydantic model) does not require a migration if the field is derived/joined from data that already exists in the DB — only new physical columns need one. Don't reflexively tell the user to run a migration for a response-shape-only change.
- **Duplicate/redundant event handlers**: when copy-pasting a pattern between two symmetric halves of a codebase (e.g. two flavors, two client types, two roles), check the source you copied *from* for accidental duplication before propagating it — don't mirror a bug into the second location.

## Efficient workflow summary

```
cody:workspace(list)                     — resolve aliases once per session
cody:find_references(symbol, context_lines=2)  — locate + disambiguate
cody:read_files(start_line, end_line)    — confirm exact surrounding code
cody:edit(old_text, new_text)            — make the change
cody:find_references(symbol)             — verify the edit landed only where intended
```

Skipping steps to save calls is a false economy — a wrong multi-match edit costs more round trips to detect and fix than the verification step would have.
