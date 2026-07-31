---
name: joshua-mcp-harness
description: Use whenever the Joshua MCP engineering harness is connected (tools task, workspace, explore, find_references, impact, read_files, edit, validate, run, git_state). Covers the enforced orient -> explore -> read -> edit -> validate -> diagnose -> repair loop, why each gate refuses, how validation plans are derived per project, how code faults are told apart from environment faults, and how to close a task honestly. Applies to any codebase reached through this server.
---

# Joshua MCP harness

This server is a workflow, not a toolbox. It tracks an active task per workspace
and refuses actions taken out of order. Every response contains a `workflow`
object — read `phase`, `outstanding` and `next_actions` and follow them.

## Open a task first

```
workspace(action="list")                     # once per session, if aliases unknown
task(workspace=W, action="start", goal=...)  # a goal is required
task(workspace=W, action="orient")           # project type, tools, git state, plan
```

`orient` is where you learn the stack. Do not assume a project is Python or that
`pytest`/`flutter`/`npm` exists — it reports exactly what is on PATH, and
`run(list_tools=True)` re-probes at any time.

## Locate before reading, read before editing

```
find_references(symbol_name=..., context_lines=2)
impact(paths=[...])
read_files(paths=[...], start_line=..., end_line=...)
```

`find_references` marks real definitions using tree-sitter, so you can tell a
declaration from a call site or an import without extra reads. `impact` returns
ranked candidates from reverse imports, symbol call sites, and co-changing
artefacts (migrations, schemas, serializers, routes, models, config, tests).
Anything it flags that you never open stays in `outstanding` until you open it or
rule it out.

`read_files(around_line=N)` is the fastest way to land on a reported failure.

## Editing

Preferred, cheapest, least ambiguous:

```
edit(path=..., start_line=..., end_line=..., new_text=<only the replacement>)
```

You never reproduce the old code. Use `old_text`/`new_text` only when you have no
reliable line numbers.

The gates and what they mean:

* **"has not been read in this task"** — call `read_files` on it first.
* **"changed on disk after you read it"** — re-read; your line numbers are stale.
* **"would replace all N lines"** — re-target at the lines that actually change,
  or split into several edits. `allow_full_rewrite=True` only when replacing the
  whole file genuinely is the task.
* **"old_text occurs N times"** — this is the classic silent-damage case. Use
  `edit(preview_matches=True)` to see every occurrence with line numbers, then
  extend `old_text` until unique or switch to a line range. `replace_all=True`
  only when every occurrence really should change.
* **"would leave brackets unbalanced"** — your replacement dropped a closing
  bracket; include it.

`function_name`/`new_function` works via tree-sitter but is less reliable than a
line range; if it cannot find the function, use `find_references(...,
definitions_only=True)` and edit by line.

Formatting is preserved automatically (line endings, trailing newline). A
`formatting` field in the response means the replacement's indentation style
differs from what it replaced — check it before validating.

## Validate by executing

```
validate()                  # syntax + static, scoped to what you edited
validate(depth="full")      # includes the test suite
validate(dry_run=True)      # see the derived plan first
validate(commands=[...])    # override when you know better
```

Each step returns command, exit code, duration, stdout and stderr. A `skipped`
entry means that check could not run here — it did **not** pass, and you must say
so when you report.

## When validation fails

The response classifies the cause:

* `classification: "code"` — re-read the reported `file:line` locations, then
  make a localized repair edit and validate again. Do not repair from memory.
* `classification: "environment"` — run the `investigate` commands with `run()`.
  If they confirm an external limitation, record it:
  `task(action="block", reason=..., evidence=["<command> -> <result>", ...])`.
  Evidence is required. Never rewrite working code to route around a missing
  tool, absent dependency, offline network or unconfigured SDK.
* `classification: "unknown"` — gather more evidence before touching the code.

After four failed attempts the harness tells you to stop repeating the same
repair and get fresh evidence instead.

## Terminal

`run(command=...)` is a real shell inside the workspace: pipes, redirection and
operators all work. Pick commands from evidence about this project — `find`, `ls`,
`tree`, `rg`, `grep`, `sed -n`, `awk`, `head`, `tail`, `wc`, `cat`, `stat`,
`file`, `diff`, `which`, `pwd`, `env`, `git`, and whichever toolchain the project
actually uses (`python3`, `pip`, `uv`, `pytest`, `ruff`, `mypy`, `dart`,
`flutter`, `npm`/`yarn`/`pnpm`, `tsc`, `cargo`, `go`, `gradle`, `mvn`, `cmake`,
`make`).

Refused: recursive deletes of `/`, block-device writes, `shutdown`, force pushes,
`git reset --hard`, `git clean -f`, piping remote scripts into a shell. Ask the
user to run those.

## Git

`git_state(action="status")` before you edit — pre-existing modifications are not
yours to claim. `git_state(action="diff")` before you report. `action="conflicts"`
lists conflicted files with marker line numbers; resolve each hunk with a
localized edit, never by overwriting the file.

## Closing

```
task(action="complete")
```

Refused if: nothing was edited, validation never ran, the last run failed,
anything was edited after the last pass, a file changed on disk outside the
harness, or blockers are recorded. If only static checks ran, either
`validate(depth="full")` or close deliberately with
`acknowledge_skipped=True` and tell the user what was not verified.

Report with executed evidence: the commands, their exit codes, and what the diff
contains. Anything unproven should be named as unproven.
