"""Translate trusted CI Docker arguments into the disposable WSL converter."""

import os
import re
import subprocess
import sys


def linux_path(value: str) -> str:
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return "/mnt/" + value[0].lower() + value[2:].replace("\\", "/")
    return value


if __name__ == "__main__":
    raise SystemExit(
        subprocess.call(
            [
                "wsl.exe",
                "-d",
                "NVX-Converter",
                "-u",
                "root",
                "--cd",
                linux_path(os.getcwd()),
                "--exec",
                "docker",
                *(linux_path(value) for value in sys.argv[1:]),
            ]
        )
    )
