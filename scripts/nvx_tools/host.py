"""Host defaults shared by the portable runtime front doors."""

import os
import sys


def default_backend() -> str:
    if os.name == "nt":
        return "whp"
    if sys.platform == "darwin":
        return "hvf"
    return "kvm"
