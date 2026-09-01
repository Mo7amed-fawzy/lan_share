#!/usr/bin/env bash
# Open the Lan-Share Relay control window (Start / Stop server buttons).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" -m client.relay_control --host 0.0.0.0 --port 8501
