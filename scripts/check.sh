#!/usr/bin/env bash
# Local gate: every offline host test is discovered automatically.
# Run from the repo root before pushing: ./scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
if python3 -m ruff --version >/dev/null 2>&1; then
    python3 -m ruff check scripts/
elif command -v ruff >/dev/null 2>&1; then
    ruff check scripts/
else
    echo "error: ruff not found; run: python3 -m pip install -r requirements-dev.txt" >&2
    exit 1
fi
python3 -m unittest discover -s scripts -p 'test_*.py'
