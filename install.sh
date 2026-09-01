#!/usr/bin/env bash
# Lan-Share installer for a new PC.
# Run from this folder on the target machine:
#
#     ./install.sh            # client PC (app + launchers)
#     ./install.sh --server   # server PC (also opens firewall port 8501)
#
# It installs the dependencies, copies Lan-Share into ~/Lan-Share and creates
# the desktop launchers, so you can start the app from the application menu.
set -euo pipefail
cd "$(dirname "$0")"

SRC="$(pwd)/lan-share"
DEST="${HOME}/Lan-Share"
SERVER_MODE=0
SKIP_DEPS=0
for arg in "$@"; do
    case "$arg" in
        --server)      SERVER_MODE=1 ;;
        --skip-deps)   SKIP_DEPS=1 ;;
        --dest=*)      DEST="${arg#*=}" ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

echo "== Lan-Share installer =="

if [ "$SKIP_DEPS" = 1 ]; then
    echo "[1/4] Skipping dependency installation (--skip-deps)."
else
    echo "[1/4] Installing dependencies (Pillow, GTK3, tkinter)..."
    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        sudo apt-get update
        sudo apt-get install -y \
            python3-pil python3-gi python3-gi-cairo \
            gir1.2-gtk-3.0 python3-tk
    else
        echo "  WARNING: no passwordless sudo - install these manually if missing:"
        echo "     sudo apt-get install python3-pil python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-tk"
    fi
fi

echo "[2/4] Copying Lan-Share to $DEST ..."
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
cp -r "$SRC" "$DEST"
find "$DEST" -name __pycache__ -type d -prune -exec rm -rf {} \; 2>/dev/null || true
chmod +x "$DEST"/scripts/*.sh

echo "[3/4] Creating desktop launchers ..."
if [ "$SERVER_MODE" = 1 ]; then
    "$DEST/scripts/install_launcher.sh" --both
else
    "$DEST/scripts/install_launcher.sh" --client
fi

if [ "$SERVER_MODE" = 1 ]; then
    echo "[4/4] Server mode: opening ports 8501 (relay) and 8502 (discovery) ..."
    if command -v ufw >/dev/null 2>&1; then
        echo "  (sudo will ask for your password to allow the ports)"
        sudo ufw allow 8501/tcp || echo "  WARNING: could not add the 8501/tcp rule."
        sudo ufw allow 8502/udp || echo "  WARNING: could not add the 8502/udp rule."
        echo "  To verify later:  sudo ufw status | grep -E '8501|8502'"
    else
        echo "  ufw not found - assuming the firewall is not blocking the ports."
    fi
else
    echo "[4/4] Client mode: done."
fi

echo
echo "== Installed into $DEST =="
echo
if [ "$SERVER_MODE" = 1 ]; then
    echo "  This PC is the relay server. Application menu:"
    echo "     Lan-Share Relay  (starts the relay on 0.0.0.0:8501)"
    echo
    echo "  Other PCs should run:  ./install.sh   (client mode only)"
    echo "  then connect with:     python3 -m client.desktop --server $(hostname -I 2>/dev/null | awk '{print $1}')"
else
    echo "  This PC is a client. Application menu:"
    echo "     Lan-Share  (share your screen / watch sessions / list sessions)"
    echo
    echo "  To connect to your relay server, find its IP first, then:"
    echo "     gtk-launch lan-share   # in the app, set the Server IP, then Start sharing"
    echo "     # or from a terminal: python3 -m client.desktop --server <SERVER_IP> --port 8501"
fi
echo
echo "  Direct commands (run from $DEST):"
echo "     python3 -m server.main --host 0.0.0.0 --port 8501"
echo "     python3 -m client.desktop --server <SERVER_IP> --port 8501"
