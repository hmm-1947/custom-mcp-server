import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"

WORKSPACES = {}
RUN_COMMANDS = {}


def load():
    global WORKSPACES, RUN_COMMANDS

    if not CONFIG_FILE.exists():
        return

    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    WORKSPACES = {
        name: Path(path)
        for name, path in data.get("workspaces", {}).items()
    }

    RUN_COMMANDS = data.get("run_commands", {})


def save():
    CONFIG_FILE.write_text(
        json.dumps(
            {
                "workspaces": {
                    name: str(path)
                    for name, path in WORKSPACES.items()
                },
                "run_commands": RUN_COMMANDS,
            },
            indent=4,
        ),
        encoding="utf-8",
    )


def add_workspace(name: str, path: str):
    p = Path(path).resolve()

    if not p.exists():
        raise FileNotFoundError(path)

    if not p.is_dir():
        raise NotADirectoryError(path)

    WORKSPACES[name] = p

    save()

def list_workspaces() -> dict[str, str]:
    return {
        name: str(path)
        for name, path in WORKSPACES.items()
    }

def remove_workspace(name: str):
    if name not in WORKSPACES:
        raise RuntimeError(f"Workspace '{name}' not found")

    WORKSPACES.pop(name, None)
    RUN_COMMANDS.pop(name, None)

    save()

def get_workspace(name: str) -> Path:
    if name not in WORKSPACES:
        raise RuntimeError(f"Workspace '{name}' not found")

    return WORKSPACES[name]

def iter_workspace_files(name: str):
    root = get_workspace(name)

    for p in root.rglob("*"):
        if p.is_file():
            yield p

def set_run_command(workspace: str, command: str):
    if workspace not in WORKSPACES:
        raise RuntimeError(f"Workspace '{workspace}' not found")

    RUN_COMMANDS[workspace] = command

    save()


def get_run_command(workspace: str):
    if workspace not in RUN_COMMANDS:
        raise RuntimeError(
            f"Run command not set for workspace '{workspace}'"
        )

    return RUN_COMMANDS[workspace]


def resolve_path(
    workspace: str,
    path: str,
) -> Path:
    root = get_workspace(workspace).resolve()

    target = (root / path).resolve()

    if target != root and root not in target.parents:
        raise PermissionError("Access outside the workspace is not allowed")

    return target


load()