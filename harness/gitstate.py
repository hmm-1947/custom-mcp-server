"""Git repository state.

The harness reads git state before it changes anything, so the model always
knows what it is standing on: which branch, what was already dirty before it
started, whether a merge is in progress, and exactly what its own edits did.
"""

from __future__ import annotations

from pathlib import Path

from . import terminal

STATUS_CODES = {
    "M": "modified", "A": "added", "D": "deleted", "R": "renamed",
    "C": "copied", "U": "unmerged", "?": "untracked", "!": "ignored", " ": "",
}


def _run(root: Path, command: str, timeout: int = 60) -> terminal.CommandResult:
    return terminal.execute(command, root, timeout=timeout)


def is_repo(root: Path) -> bool:
    return _run(root, "git rev-parse --is-inside-work-tree").ok


def parse_status(porcelain: str) -> list[dict]:
    entries: list[dict] = []
    for line in porcelain.splitlines():
        if len(line) < 3:
            continue
        index_code, worktree_code, path = line[0], line[1], line[3:]
        original = None
        if " -> " in path:
            original, path = path.split(" -> ", 1)
        entries.append({
            "path": path.strip('"'),
            "renamed_from": original,
            "index": STATUS_CODES.get(index_code, index_code),
            "worktree": STATUS_CODES.get(worktree_code, worktree_code),
            "staged": index_code not in " ?",
            "conflicted": "U" in (index_code + worktree_code) or index_code + worktree_code in ("AA", "DD"),
        })
    return entries


def status(root: Path) -> dict:
    if not is_repo(root):
        return {"is_repo": False, "note": "Not a git repository; no version-control safety net here."}

    porcelain = _run(root, "git status --porcelain=v1 --untracked-files=normal")
    branch = _run(root, "git rev-parse --abbrev-ref HEAD")
    upstream = _run(root, "git rev-parse --abbrev-ref --symbolic-full-name @{u}")
    ahead_behind = _run(root, "git rev-list --left-right --count @{u}...HEAD") if upstream.ok else None
    conflicts = _run(root, "git diff --name-only --diff-filter=U")
    merging = (root / ".git" / "MERGE_HEAD").exists()
    rebasing = (root / ".git" / "rebase-merge").exists() or (root / ".git" / "rebase-apply").exists()

    entries = parse_status(porcelain.stdout)
    ahead = behind = None
    if ahead_behind is not None and ahead_behind.ok:
        parts = ahead_behind.stdout.split()
        if len(parts) == 2:
            behind, ahead = int(parts[0]), int(parts[1])

    return {
        "is_repo": True,
        "branch": branch.stdout.strip() or None,
        "upstream": upstream.stdout.strip() if upstream.ok else None,
        "ahead": ahead,
        "behind": behind,
        "dirty_count": len(entries),
        "entries": entries[:100],
        "staged": [entry["path"] for entry in entries if entry["staged"]],
        "unstaged": [entry["path"] for entry in entries if entry["worktree"] and not entry["staged"]],
        "untracked": [entry["path"] for entry in entries if entry["worktree"] == "untracked"],
        "conflicts": [line for line in conflicts.stdout.splitlines() if line],
        "merge_in_progress": merging,
        "rebase_in_progress": rebasing,
    }


def diff(root: Path, paths: list[str] | None = None, staged: bool = False, stat_only: bool = False,
         context: int = 3) -> dict:
    scope = " ".join(f"'{path}'" for path in paths or [])
    flags = "--cached" if staged else ""
    shape = "--stat" if stat_only else f"--unified={context}"
    result = _run(root, f"git diff {flags} {shape} -- {scope}".strip(), timeout=120)
    body, clipped = terminal.clip(result.stdout, 20000)
    return {
        "command": result.command,
        "diff": body,
        "clipped": clipped,
        "empty": not result.stdout.strip(),
        "exit_code": result.exit_code,
    }


def log(root: Path, count: int = 10, path: str | None = None) -> dict:
    scope = f" -- '{path}'" if path else ""
    result = _run(root, f"git log --oneline --decorate -n {int(count)}{scope}")
    return {"log": result.stdout.strip().splitlines(), "exit_code": result.exit_code}


def branches(root: Path) -> dict:
    local = _run(root, "git branch --format='%(refname:short)|%(upstream:short)|%(committerdate:relative)'")
    current = _run(root, "git rev-parse --abbrev-ref HEAD")
    parsed = []
    for line in local.stdout.splitlines():
        parts = line.strip("'").split("|")
        if parts and parts[0]:
            parsed.append({
                "name": parts[0],
                "upstream": parts[1] if len(parts) > 1 and parts[1] else None,
                "last_commit": parts[2] if len(parts) > 2 else None,
            })
    return {"current": current.stdout.strip(), "branches": parsed}


def conflict_details(root: Path) -> dict:
    unmerged = _run(root, "git diff --name-only --diff-filter=U")
    files = [line for line in unmerged.stdout.splitlines() if line]
    details = []
    for path in files[:20]:
        markers = _run(root, f"grep -n '^\\(<<<<<<<\\|=======\\|>>>>>>>\\)' '{path}'")
        details.append({
            "path": path,
            "marker_lines": [line for line in markers.stdout.splitlines() if line][:40],
        })
    return {
        "conflicted_files": files,
        "details": details,
        "note": (
            "Resolve conflicts by reading each hunk and editing the conflicted region; "
            "never resolve by overwriting the whole file."
        ) if files else "No merge conflicts.",
    }


def changed_paths(root: Path) -> list[str]:
    result = _run(root, "git status --porcelain=v1 --untracked-files=normal")
    return [entry["path"] for entry in parse_status(result.stdout)]
