#!/usr/bin/env bash
# Local gate for agent commits: fast lint plus the two live test files.
# Run from the repo root before pushing: ./scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ruff check scripts/
python3 -m unittest scripts/test_nvx_9p.py scripts/test_control_session.py
