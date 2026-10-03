"""Check all release platform proofs and collect one copy of each verified asset."""

import argparse
import subprocess
from pathlib import Path

from nvx_tools.release_gate import verify_matrix

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("root", type=Path)
parser.add_argument("--revision", required=True)
parser.add_argument("--version", required=True)
parser.add_argument("--destination", type=Path, default=Path("build/publish"))
args = parser.parse_args()
core = subprocess.check_output(["git", "rev-parse", "HEAD:openvmm"], text=True).strip()
verify_matrix(args.root, args.revision, core, args.version, args.destination)
