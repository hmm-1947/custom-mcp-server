import subprocess
import threading

class TerminalProcess:

    def __init__(self, command: str, cwd=None):

        self.command = command

        self.stdout_history = []
        self.stderr_history = []

        self.process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
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

    def output(self, tail: int | None = 200):
        stdout = self.stdout_history[-tail:] if tail else self.stdout_history
        stderr = self.stderr_history[-tail:] if tail else self.stderr_history

        return {
            "running": self.process.poll() is None,
            "exit_code": self.process.poll(),
            "stdout": "\n".join(stdout),
            "stderr": "\n".join(stderr),
            "stdout_total_lines": len(self.stdout_history),
            "stderr_total_lines": len(self.stderr_history),
        }
    

    def tail_stdout(self, lines: int = 20):
        return "\n".join(list(self.stdout_history)[-lines:])

    def tail_stderr(self, lines: int = 20):
        return "\n".join(list(self.stderr_history)[-lines:])
    def tail(
        self,
        stdout_cursor: int = 0,
        stderr_cursor: int = 0,
    ):
        return {
            "running": self.is_running(),
            "exit_code": self.process.poll(),

            "stdout_cursor": len(self.stdout_history),
            "stderr_cursor": len(self.stderr_history),

            "stdout": self.stdout_history[stdout_cursor:],
            "stderr": self.stderr_history[stderr_cursor:],
        }
    

    def wait(self, tail: int | None = 200):
        self.process.wait()
        return self.output(tail)
    
    
    def is_running(self):
        return self.process.poll() is None
    def stop(self):
        if self.is_running():
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()

        return {
            "success": True,
            "exit_code": self.process.returncode,
        }