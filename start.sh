#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON="$ROOT/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  printf '%s\n' 'Run first: python3 scripts/install.py (Python 3.11-3.13).'
  exit 1
fi
exec "$PYTHON" -m trace_llm --open-browser "$@"
