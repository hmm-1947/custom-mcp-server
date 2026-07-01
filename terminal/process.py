import subprocess


class TerminalProcess:

    def __init__(self, command: str, cwd=None):

        self.command = command

        self.process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
        )

    def output(self):
        stdout, stderr = self.process.communicate()

        return {
            "exit_code": self.process.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }