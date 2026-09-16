#!/usr/bin/env bash
# Install desktop launchers for Lan-Share.
#   --client (default)  : "Lan-Share" client app only
#   --server            : "Lan-Share Relay" server launcher only
#   --both              : both
# Adds them to the application menu so no terminal is needed to start them.
#
# When the .deb package is installed (/usr/bin/lan-share exists) the system
# ships proper launchers under /usr/share/applications/, so user-level ones
# are skipped entirely and any stale copies (which would shadow the system
# entries and launch old code) are removed.
set -euo pipefail

MODE=client
case "${1:-}" in
    --client) MODE=client ;;
    --server) MODE=server ;;
    --both)   MODE=both ;;
    "")       MODE=client ;;
    *) echo "usage: $0 [--client|--server|--both]" >&2; exit 2 ;;
esac

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
APP_DIR="$HOME/.local/share/applications"
mkdir -p "$APP_DIR"

# Remove any launchers a previous run of this script created, so they never
# shadow the system .desktop entries (which are the single source of truth
# when the .deb is installed).
rm -f "$APP_DIR/lan-share.desktop" "$APP_DIR/lan-share-relay.desktop"

if command -v lan-share >/dev/null 2>&1 || command -v lan-share-relay >/dev/null 2>&1; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APP_DIR" 2>/dev/null || true
    fi
    echo "Lan-Share .deb is installed - using the system launchers from the application menu."
    echo "  (stale user-level launchers in $APP_DIR were removed)"
    exit 0
fi

install_client() {
    cat > "$APP_DIR/lan-share.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Lan-Share
GenericName=Screen sharing
Comment=Share your screen or watch sessions on your LAN
Exec=$ROOT/scripts/run_desktop.sh
Icon=$ROOT/client/lan_share.png
Terminal=false
Categories=Network;
StartupWMClass=Lan-Share
Keywords=screen;share;stream;lan;remote;
EOF
    chmod +x "$APP_DIR/lan-share.desktop"
    echo "  - Lan-Share (client app) -> $APP_DIR/lan-share.desktop"
}

install_server() {
    cat > "$APP_DIR/lan-share-relay.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Lan-Share Relay
GenericName=Screen sharing server
Comment=Start or stop the Lan-Share relay server on this PC (open to the LAN)
Exec=$ROOT/scripts/run_relay_control.sh
Icon=$ROOT/client/lan_share_server.png
Terminal=false
Categories=Network;
StartupWMClass=Lan-Share Relay
Keywords=server;relay;screen;share;stream;lan;
EOF
    chmod +x "$APP_DIR/lan-share-relay.desktop"
    echo "  - Lan-Share Relay (server control) -> $APP_DIR/lan-share-relay.desktop"
}

case "$MODE" in
    client) install_client ;;
    server) install_server ;;
    both)   install_client; install_server ;;
esac

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" || true
fi

echo "Desktop launcher(s) installed - look for them in the application menu."