import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"

WORKSPACE = None
RUN_COMMAND = None


def load():
    global WORKSPACE, RUN_COMMAND

    if not CONFIG_FILE.exists():
        return

    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    workspace = data.get("workspace")
    if workspace:
        WORKSPACE = Path(workspace)

    RUN_COMMAND = data.get("run_command")


def save():
    CONFIG_FILE.write_text(
        json.dumps(
            {
                "workspace": str(WORKSPACE) if WORKSPACE else None,
                "run_command": RUN_COMMAND,
            },
            indent=4,
        ),
        encoding="utf-8",
    )


def set_workspace(path: str):
    global WORKSPACE

    p = Path(path).resolve()

    if not p.exists():
        raise FileNotFoundError(path)

    if not p.is_dir():
        raise NotADirectoryError(path)

    WORKSPACE = p

    save()


def get_workspace() -> Path:
    if WORKSPACE is None:
        raise RuntimeError("Workspace not set")

    return WORKSPACE


def set_run_command(command: str):
    global RUN_COMMAND

    RUN_COMMAND = command

    save()


def get_run_command():
    if RUN_COMMAND is None:
        raise RuntimeError("Run command not set")

    return RUN_COMMAND


def resolve_path(path: str) -> Path:
    workspace = get_workspace().resolve()

    target = (workspace / path).resolve()

    if target != workspace and workspace not in target.parents:
        raise PermissionError("Access outside the workspace is not allowed")

    return target


load()