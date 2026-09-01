#!/usr/bin/env bash
# Launch the Lan-Share desktop app (GTK3 window).
# Mirrors the old Flutter bundle app GUI, works with the current relay server.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" -m client.desktop "$@"
