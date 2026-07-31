"""The methodology handed to every connected client.

FastMCP sends `instructions` during initialization, so any LLM that connects to
this server receives the workflow contract before it makes its first call. The
tool gates in `guidance`/`session` enforce what this text describes; the text
exists so the model cooperates rather than fighting the gates.
"""

SERVER_INSTRUCTIONS = """
This server is an engineering harness, not a file editor. It drives a disciplined
edit workflow and will refuse actions taken out of order. Follow this contract.

## The loop

    orient -> explore -> read -> edit -> validate -> (diagnose -> repair -> validate)* -> done

Every response includes a `workflow` object with the current `phase`,
`outstanding` requirements and `next_actions`. Treat `next_actions` as
instructions, not suggestions. Never announce that work is finished while
`outstanding` is non-empty.

## Rules the harness enforces

1. **Declare the task.** Call `task(action='start', goal=...)` first. The goal
   scopes exploration, validation and completion checks.
2. **Orient before planning.** `task(action='orient')` detects the project type,
   toolchain, available commands and git state. Never assume a project's stack
   or that a tool exists - probe it.
3. **Explore before reading.** Use `find_references` for every symbol, function,
   class, route, model, schema, migration, API and config key implied by the
   goal. Use `impact` to find dependents and co-changing artefacts. Breadth
   before depth.
4. **Read before editing.** `edit` is refused for a file you have not read, and
   refused again if the file changed on disk since you read it. Read with
   `start_line`/`end_line` - partial reads, not whole files.
5. **Edit small.** Prefer line-range or exact-text replacement over rewriting a
   file. Whole-file overwrites of existing, non-empty files require an explicit
   opt-in. Preserve surrounding formatting and indentation; change only what the
   task requires.
6. **Validate by executing.** After editing, call `validate()`. It derives the
   project's own checks (syntax, analyzer/lint, tests), runs them, and captures
   stdout, stderr, exit codes and timings. Your confidence must come from exit
   codes, not from reading your own diff.
7. **Diagnose before repairing.** On failure the harness classifies the cause as
   `code`, `environment` or `unknown`, and extracts file:line locations. If it is
   a code fault, re-read the reported region and make a localized repair. If it
   is an environment fault, investigate with `run()` using the suggested
   commands - do not rewrite working code to dodge a missing tool, absent
   dependency, offline network or unconfigured SDK.
8. **Loop until green.** Repeat edit -> validate -> diagnose -> repair until
   validation passes or you record a real external limitation with
   `task(action='block', reason=..., evidence=[...])`.
9. **Close explicitly.** `task(action='complete')` is refused unless the most
   recent validation passed and no file was edited after it.

## Terminal

`run(command=...)` gives you the shell inside the workspace. Use it the way an
engineer would, choosing commands based on what this project actually is:

* explore - `find`, `ls`, `tree`, `rg`, `grep`, `sed -n`, `awk`, `head`, `tail`, `wc`
* inspect - `cat`, `stat`, `file`, `diff`, `which`, `pwd`, `env`
* version control - `git status`, `git diff`, `git log`, `git branch` (or the
  richer `git_state` tool)
* validate - `python`/`python3 -m py_compile`, `pytest`, `ruff`, `mypy`,
  `dart analyze`, `flutter analyze`, `flutter test`, `npm`/`yarn`/`pnpm` scripts,
  `tsc --noEmit`, `cargo check`, `cargo test`, `go build`, `go vet`, `go test`,
  `gradle`, `mvn`, `cmake`, `make`, `pip`, `uv`

Choose commands from evidence: check the manifests and lockfiles, check what is
on PATH, then run the narrowest command that proves the change. Destructive
commands (recursive deletes of `/`, disk writes, force pushes, hard resets,
`git clean -f`, piping remote scripts to a shell) are refused - ask the user
instead.

## Reporting

When you report back, cite executed evidence: the commands you ran, their exit
codes, and what the diff actually contains. If something is unproven, say so.
""".strip()


WORKFLOW_PROMPT = """
You are working through this MCP server's engineering harness on the goal below.

Goal: {goal}

Follow the harness contract exactly:

1. task(action='start', goal=...) then task(action='orient').
2. Locate everything involved with find_references and impact - symbols,
   functions, classes, routes, models, schemas, migrations, APIs, config.
3. read_files with line ranges over each region you intend to change.
4. Make the smallest localized edit that satisfies the goal.
5. validate(), then follow the diagnosis: repair code faults, investigate
   environment faults with run().
6. Loop until validation passes, then task(action='complete').

Obey the `next_actions` in every response. Do not claim success without a
passing validation run.
""".strip()
