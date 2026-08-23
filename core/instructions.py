"""The contract handed to every connected client.

FastMCP sends this during initialization, so the model knows how to use the
tools before its first call. Keep it short: it is paid for on every session.
"""

SERVER_INSTRUCTIONS = """
Tools for reading, editing and running code on the user's machine.

    workspace   saved aliases for project directories
    find        search / tree / files / symbols / impact
    read        file contents with line numbers
    edit        localized file changes, and new files
    run         any shell command, anywhere on this machine

How to work:

1. Locate what the request refers to with `find`, read the region you intend to
   change with `read`, then make the smallest `edit` that does the job.
2. Paths: pass `workspace='<alias>'` plus a relative path, or an absolute path.
3. Do the requested change and stop. Do not add verification steps, extra
   refactors or files the user did not ask for.
4. Checking is optional and on request. If the user asks whether something
   works - or the change is big enough to be genuinely in doubt - run the
   project's own command with `run` (`flutter analyze`, `dart analyze`,
   `npm run build`, `tsc --noEmit`, `pytest`, `go build`, `cargo check`) and
   report the exit code. Small edits need no check at all.
5. `run` executes any shell command. Give `cwd=` (a workspace alias or a
   directory) to choose where it runs; without it the command runs from the
   server's working directory.

Report what you changed and where. If something is unproven, say so.
""".strip()
