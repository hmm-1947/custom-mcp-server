# Joshua MCP — an engineering harness

This is not a passive file editor. It is a harness that drives a connected LLM
through a disciplined engineering workflow and refuses actions taken out of
order.

```
orient -> explore -> read -> edit -> validate -> (diagnose -> repair -> validate)* -> done
```

Every tool call reports into an active task. Every response carries a `workflow`
envelope with the current phase, what is still `outstanding`, and the
`next_actions` required. The methodology is also sent to the client as the
server's MCP `instructions`, so a model receives the contract before its first
call.

## What the harness enforces

| Gate | Why |
|---|---|
| `edit` refuses a file that was never read | an edit from memory is a guess |
| `edit` refuses a file that changed on disk since the read | stale line numbers corrupt files |
| `edit` refuses a whole-file rewrite of an existing file | localized changes preserve unrelated code |
| `edit` refuses an `old_text` that matches more than once | silent multi-site rewrites are the most common damage |
| `edit` refuses a change that unbalances brackets | cheap structural check before writing |
| `task(complete)` refuses unless the last validation passed | confidence must come from exit codes |
| `task(complete)` refuses if anything was edited after that pass | a change after a green run is unproven |
| `task(complete)` surfaces checks that were skipped | a skipped test is not a passing test |
| `task(block)` refuses without executed evidence | "the environment is broken" needs proof |

Line endings, indentation style and the trailing newline are preserved, and
formatting drift is reported rather than silently applied.

## Tools

| Tool | Role |
|---|---|
| `task` | start / orient / status / next / note / block / unblock / complete / abandon |
| `workspace` | register and resolve project roots |
| `explore` | repository layout, file lists, symbol inventory |
| `find_references` | locate a symbol everywhere, with context and tree-sitter definitions |
| `impact` | reverse imports, symbol call sites, co-changing migrations/schemas/routes/config/tests |
| `read_files` | partial reads with line numbers, recorded in the task ledger |
| `edit` | localized line-range, exact-text, function or new-file edits, gated |
| `validate` | derives and runs the project's own checks, then diagnoses failures |
| `run` | full shell access inside the workspace |
| `git_state` | status, diff, staged diff, log, branch, conflicts, overview |

## Validation

`validate()` reads the repository's own markers — manifests, lockfiles, script
definitions, test layout — checks what is actually installed, and derives a
tiered plan that runs cheapest-first:

* `syntax` — `py_compile`, `tsc --noEmit`, `cargo check`, `go build`, compile targets
* `static` — `ruff`, `mypy`, `dart analyze`, `dart format --set-exit-if-changed`, project `lint`/`typecheck` scripts, `go vet`, `clippy`
* `test` — `pytest` (or the stdlib `unittest` runner when pytest is absent), `flutter test`, `npm test`, `cargo test`, `go test`, gradle/maven

Depth is selectable (`quick`, `standard`, `full`). Steps whose tool is missing are
reported as skipped with a reason rather than dropped. Every step captures
stdout, stderr, exit code and duration.

## Failure diagnosis

On failure the harness classifies the cause and says what to do:

* **code** — extracts `file:line` locations from the toolchain output and sends
  the model back to `read_files` before it may repair
* **environment** — missing executable, absent dependency, PEP 668 interpreter,
  offline network, disk full, permissions, unconfigured SDK, docker daemon down,
  database unreachable, timeout — and proposes the shell commands that confirm
  it. Working code is not rewritten to dodge an environment fault.
* **unknown** — instructs the model to gather more evidence rather than assume
  the implementation is wrong.

## Layout

```
server.py              entry point; advertises the methodology as MCP instructions
tools.py               tool registration + the engineering_task prompt
config.py              workspaces and path resolution
harness/
  phases.py            the workflow definition
  session.py           per-task ledger (searched/read/edited, digests, history) + phase machine
  guidance.py          turns ledger state into the next required action
  terminal.py          the only place a command is executed
  ripgrep.py           the only place a search is executed
  project.py           project detection + validation-plan derivation
  diagnose.py          code vs environment failure classification
  impact.py            what else a change affects
  gitstate.py          repository state
  structure.py         bracket-balance guard for edits
  instructions.py      the contract sent to every client
tool_handlers/         thin adapters: validate input, call the harness, attach guidance
code_engine/           tree-sitter parsing, symbol lookup, function replacement
```

## Running

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python server.py                      # streamable-http on 127.0.0.1:8001
venv/bin/python server.py --transport stdio    # for stdio clients
venv/bin/python server.py --port 9000
```

Expose it to a remote client with a tunnel (e.g. ngrok) and add the URL to the
client's custom MCP configuration.
