#!/usr/bin/env bash
# Watch a screen shared through the Lan-Share server.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" -m client.main view "$@"
