#!/usr/bin/env bash
# Open the Lan-Share client GUI manager (browser-based control panel).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" -m client.manager --open "$@"