import subprocess
import threading
from collections import deque

class TerminalProcess:

    def __init__(self, command: str, cwd=None):

        self.command = command

        self.stdout_history = deque(maxlen=5000)
        self.stderr_history = deque(maxlen=5000)

        self.process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        threading.Thread(
            target=self._read_stdout,
            daemon=True,
        ).start()

        threading.Thread(
            target=self._read_stderr,
            daemon=True,
        ).start()

    def _read_stdout(self):
        for line in self.process.stdout:
            self.stdout_history.append(line.rstrip())

    def _read_stderr(self):
        for line in self.process.stderr:
            self.stderr_history.append(line.rstrip())

    def output(self):
        return {
            "running": self.process.poll() is None,
            "exit_code": self.process.poll(),
            "stdout": "\n".join(self.stdout_history),
            "stderr": "\n".join(self.stderr_history),
        }
    def tail_stdout(self, lines: int = 20):
        return "\n".join(list(self.stdout_history)[-lines:])

    def tail_stderr(self, lines: int = 20):
        return "\n".join(list(self.stderr_history)[-lines:])
    def wait(self):
        self.process.wait()
        return self.output()