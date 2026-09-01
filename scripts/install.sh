#!/usr/bin/env bash
# Install Lan-Share dependencies (Debian/Ubuntu).
# Handles PEP 668 "externally-managed-environment" by preferring a local venv
# and falling back to apt packages (python3-pil, python3-tk).
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY=python3

# 1. Pillow ---------------------------------------------------------------
if "$PY" -c "import PIL; print('Pillow available:', PIL.__version__)" 2>/dev/null; then
    echo ">> Pillow already available, skipping install."
else
    echo ">> Pillow not found - trying a local virtual environment..."
    if "$PY" -m venv "$ROOT/.venv" 2>/dev/null; then
        "$ROOT/.venv/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true
        "$ROOT/.venv/bin/python" -m pip install -r requirements.txt
        PY="$ROOT/.venv/bin/python"
        echo ">> Installed into $ROOT/.venv (run scripts use it automatically)."
    else
        echo ">> venv unavailable - falling back to the system Pillow package."
        if command -v sudo >/dev/null 2>&1; then
            sudo apt-get update
            sudo apt-get install -y python3-pil
        else
            echo "!! Cannot install Pillow. As root run: apt-get install python3-pil"
        fi
    fi
fi

# 2. tkinter (needed only for the viewer window) --------------------------
if "$PY" -c "import tkinter" 2>/dev/null; then
    echo ">> tkinter available."
else
    echo ">> tkinter not found - installing python3-tk for the viewer window..."
    if command -v sudo >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y python3-tk
    else
        echo "!! Please install python3-tk manually to view streams:"
        echo "   sudo apt-get install python3-tk"
    fi
fi

echo ">> Done. Start the server first, then share/view (no sudo needed):"
echo "   ./scripts/run_server.sh"
echo "   ./scripts/run_gui.sh --open            # browser GUI (recommended)"
echo "   ./scripts/run_client.sh --server <SERVER_IP>"
echo "   ./scripts/run_viewer.sh --server <SERVER_IP>"
