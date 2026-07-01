from .process import TerminalProcess


class TerminalManager:

    def __init__(self):
        self.processes = {}
        self.next_id = 1

    def run(self, command: str, cwd=None) -> int:
        process = TerminalProcess(command, cwd)

        pid = self.next_id
        self.next_id += 1

        self.processes[pid] = process

        return pid

    def get(self, pid: int):
        if pid not in self.processes:
            raise ValueError("Invalid process id")

        return self.processes[pid]


manager = TerminalManager()