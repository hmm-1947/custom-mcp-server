# MCP Local Code Editing Server

An MCP server that lets a connected LLM read, edit and run code on this machine.
It is a small, direct toolbox: locate the code, read it, change it, and stop.
There is no enforced workflow, no validation stage and no repair loop, so a
change costs only the tokens the change actually needs.

## Tools

| Tool | Role |
|---|---|
| `workspace` | list / add / remove saved project directories (aliases) |
| `find` | `search`, `tree`, `files`, `symbols`, `impact` |
| `read` | file contents with line numbers, whole file or a region |
| `edit` | line-range, exact-text, function or whole-file changes; creates files |
| `run` | any shell command, in any directory on this machine |

Paths: pass `workspace='<alias>'` with a relative path, or an absolute path.
Neither is required by `run`.

## How a change is expected to go

```
find -> read -> edit -> report
```

Checking is optional and on request. If the user asks whether something works,
or the change is large enough to be in doubt, the model runs the project's own
command with `run` (`flutter analyze`, `dart analyze`, `pytest`, `npm run
build`, `tsc --noEmit`, `go build`, `cargo check`) and reports the exit code.
Small edits need no check at all.

## What `edit` still refuses

Only the two cases where an automated editor silently damages a file:

* an `old_text` that matches more than once - it returns every occurrence with
  line numbers instead of guessing (`replace_all=True` to override)
* a replacement that would leave brackets unbalanced in a C-family or Dart file

Line endings, indentation and the trailing newline are preserved, and
formatting drift is reported rather than silently applied.

`run` refuses a short list of catastrophic commands (recursive deletes of `/`,
disk formats, force pushes, `git reset --hard`, `git clean -f`, piping a remote
script into a shell). Everything else runs.

## Layout

```
server.py              entry point; sends the usage contract as MCP instructions
tools.py               tool registration
config.py              workspaces and path resolution
core/
  terminal.py          the only place a command is executed
  search.py            the only place a search is executed (ripgrep, with fallback)
  structure.py         bracket-balance guard for edits
  impact.py            what else a change may affect (used by find(action='impact'))
  instructions.py      the contract sent to every client
tool_handlers/         one module per tool
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

On Windows use `venv\Scripts\python.exe server.py`.

Expose it to a remote client with a tunnel (e.g. ngrok) and add the URL to the
client's custom MCP configuration.
