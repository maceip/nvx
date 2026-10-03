#!/usr/bin/env bash
# Local gate: every offline host test is discovered automatically.
# Run from the repo root before pushing: ./scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m ruff check scripts/
python3 -m unittest discover -s scripts -p 'test_*.py'
