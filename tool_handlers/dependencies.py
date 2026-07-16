import os
from pathlib import Path
from typing import Iterator


DEPENDENCY_DIRS = frozenset({
    ".dart_tool", ".env", ".git", ".idea", ".mypy_cache", ".pub-cache",
    ".pytest_cache", ".venv", ".vscode", "__pycache__", "build", "dist", "env", "node_modules",
    "site-packages", "target", "venv",
})


def is_dependency_path(path: Path) -> bool:
    return any(part in DEPENDENCY_DIRS for part in path.parts)


def iter_files(root: Path, exclude_deps: bool = True) -> Iterator[Path]:
    """Yield files below root, optionally pruning dependency/build directories."""
    for directory, directories, filenames in os.walk(root):
        if exclude_deps:
            directories[:] = [name for name in directories if name not in DEPENDENCY_DIRS]
        for filename in filenames:
            yield Path(directory) / filename


def ripgrep_excludes() -> list[str]:
    """Return ripgrep glob arguments that prune dependency directories."""
    return [flag for directory in DEPENDENCY_DIRS for flag in ("-g", f"!**/{directory}/**")]
