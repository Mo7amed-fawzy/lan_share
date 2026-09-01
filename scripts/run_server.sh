#!/usr/bin/env bash
# Start the Lan-Share relay server.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" -m server.main "$@"
