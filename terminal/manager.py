from .process import TerminalProcess


from .process import TerminalProcess


class TerminalManager:

    def __init__(self):
        self.processes = {}

    def run(self, command: str, cwd=None, name: str | None = None) -> str:
        if name is None:
            raise ValueError("Terminal name is required")

        if name in self.processes:
            old = self.processes[name]

            if old.is_running():
                raise ValueError(f"Terminal '{name}' is already running")

        self.processes[name] = TerminalProcess(command, cwd)

        return name

    def get(self, name: str):
        if name not in self.processes:
            raise ValueError(f"Terminal '{name}' does not exist")

        return self.processes[name]

    def tail(
        self,
        name: str,
        stdout_cursor: int = 0,
        stderr_cursor: int = 0,
    ):
        return self.get(name).tail(
            stdout_cursor,
            stderr_cursor,
        )

    def wait(self, name: str, tail: int | None = 200):
        return self.get(name).wait(tail)

    def is_running(self, name: str):
        return self.get(name).is_running()

    def stop(self, name: str):
        return self.get(name).stop()

    def list(self):
        return [
            {
                "name": name,
                "running": process.is_running(),
            }
            for name, process in self.processes.items()
        ]



manager = TerminalManager()