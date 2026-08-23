"""Small shared helpers for the tool handlers."""

from __future__ import annotations

from pathlib import Path


def rel(root: Path | None, path: Path) -> str:
    """Root-relative POSIX path when possible, absolute otherwise."""
    if root is not None:
        try:
            return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            pass
    return Path(path).as_posix()


def numbered(lines: list[str], first: int, last: int) -> str:
    if not lines:
        return "[empty file]"
    return "\n".join(f"{number:>5} | {lines[number - 1]}" for number in range(first, last + 1))


def window(content: str, start: int, end: int, padding: int) -> str:
    """A numbered slice of `content` around lines start..end."""
    lines = content.splitlines()
    if not lines:
        return "[empty file]"
    first = max(1, start - padding)
    last = min(len(lines), end + padding)
    return f"[lines {first}-{last} of {len(lines)}]\n{numbered(lines, first, last)}"
