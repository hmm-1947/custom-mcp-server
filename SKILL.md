---
name: local-code-editing
description: Use whenever the MCP local code editing server is connected (tools workspace, find, read, edit, run). Covers how to locate code, edit it precisely, create files, run commands anywhere on the machine, and when a change is worth verifying. Applies to any codebase reached through this server.
---

# Local code editing

Five tools: `workspace`, `find`, `read`, `edit`, `run`. Locate, read, change,
report. Nothing is enforced and nothing is staged - do the requested change and
stop.

## Paths

Pass `workspace='<alias>'` with relative paths, or absolute paths. Use
`workspace(action='list')` once if the aliases are unknown, or
`workspace(action='add', name=..., path=...)` to register a new root.

## Locate

```
find(action='search', query='handleLogin', workspace=W)      # every occurrence + definitions
find(action='search', query='api/v\\d+', regex=True)          # pattern
find(action='search', query='Foo', file_glob='*.dart')       # narrowed
find(action='tree', workspace=W)                             # layout
find(action='files', workspace=W, path='lib')                # paths
find(action='symbols', workspace=W, path='lib/auth.dart')    # functions/classes
find(action='impact', workspace=W, paths=['lib/user.dart'])  # dependents
```

`search` marks real definitions with tree-sitter, so a declaration is
distinguishable from a call site without another read.

## Read

```
read(paths=['lib/auth.dart'], workspace=W, start_line=40, end_line=120)
read(paths=['lib/auth.dart'], workspace=W, around_line=88)
```

Read the region you are about to change. The numbers returned are the ones
`edit` expects.

## Edit

Preferred - you never reproduce the old code:

```
edit(path='lib/auth.dart', workspace=W, start_line=88, end_line=94,
     new_text='<just the replacement>')
```

Other forms:

```
edit(path=..., old_text=..., new_text=...)          # when line numbers are unknown
edit(path=..., function_name=..., new_function=...) # whole function, via tree-sitter
edit(path=..., content=...)                         # create a file, or rewrite one
edit(path=..., old_text=..., preview=True)          # list occurrences, change nothing
edit(..., dry_run=True)                             # see the result without writing
```

Two refusals, both worth respecting:

* **"old_text occurs N times"** - every occurrence is returned with line
  numbers. Extend `old_text` until unique, switch to a line range, or pass
  `replace_all=True` if all of them really should change.
* **"would leave brackets unbalanced"** - the replacement dropped a closing
  bracket; include it.

A `formatting` field means the replacement's indentation differs from what it
replaced. Line endings and the trailing newline are preserved automatically.

## Run

```
run(command='flutter analyze', cwd=W)
run(command='git diff', cwd='D:/projects/app')
run(command='where flutter')
```

`cwd` takes a workspace alias or a directory; without it the command runs from
the server's working directory. Pipes, redirection and operators work.
Catastrophic commands (recursive deletes of `/`, disk formats, force pushes,
`git reset --hard`, `git clean -f`, piping remote scripts into a shell) are
refused - ask the user to run those.

## When to verify

Only when asked, or when the change is big enough to be genuinely in doubt.
Then run the project's own command and report the exit code:

| Stack | Command |
|---|---|
| Flutter / Dart | `flutter analyze`, `dart analyze`, `flutter test` |
| Python | `python -m py_compile <file>`, `ruff check`, `pytest` |
| Node / TypeScript | `npm run build`, `tsc --noEmit`, `npm test` |
| Go | `go build ./...`, `go vet ./...` |
| Rust | `cargo check`, `cargo test` |

A one-line edit needs no check. Do not invent a verification loop, and do not
rewrite working code to get around a missing tool - report the missing tool.

## Reporting

Say what changed and where. If a check ran, give the command and its exit code.
If nothing was verified, say that too.
