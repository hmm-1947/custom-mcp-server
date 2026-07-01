from pathlib import Path

WORKSPACE = None


def set_workspace(path: str):
    global WORKSPACE

    p = Path(path).resolve()

    if not p.exists():
        raise FileNotFoundError(path)

    if not p.is_dir():
        raise NotADirectoryError(path)

    WORKSPACE = p

RUN_COMMAND = None


def set_run_command(command: str):
    global RUN_COMMAND
    RUN_COMMAND = command


def get_run_command():
    if RUN_COMMAND is None:
        raise RuntimeError("Run command not set")

    return RUN_COMMAND


def get_workspace() -> Path:
    if WORKSPACE is None:
        raise RuntimeError("Workspace not set")

    return WORKSPACE

def resolve_path(path: str) -> Path:
    workspace = get_workspace().resolve()

    target = (workspace / path).resolve()

    if target != workspace and workspace not in target.parents:
        raise PermissionError("Access outside the workspace is not allowed")

    return target