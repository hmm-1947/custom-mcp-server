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
    def tail(
        self,
        pid: int,
        stdout_cursor: int = 0,
        stderr_cursor: int = 0,
    ):
        return self.get(pid).tail(
            stdout_cursor,
            stderr_cursor,
        )
    
    def wait(self, pid: int):
        return self.get(pid).wait()
    
    def is_running(self, pid: int):
        return self.get(pid).is_running()
    
    def stop(self, pid: int):
        return self.get(pid).stop()


manager = TerminalManager()