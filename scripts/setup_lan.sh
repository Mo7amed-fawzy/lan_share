#!/usr/bin/env bash
# One-step setup for running Lan-Share across Linux machines on the LAN.
# Installs the runtime packages (Pillow, GTK3, tkinter) and opens the relay +
# discovery ports in the firewall. Afterwards every command is typed directly
# in the terminal from the Lan-Share folder - no wrapper scripts needed.
set -euo pipefail

cd "$(dirname "$0")/.."
echo "== Lan-Share LAN setup ($(hostname)) =="

echo
echo "[1/2] Installing packages (Pillow, GTK3 desktop app, tkinter viewer)..."
sudo apt-get update
sudo apt-get install -y python3-pil python3-gi python3-gi-cairo \
    gir1.2-gtk-3.0 python3-tk

# Open one TCP or UDP port with whichever firewall tool the distro uses.
open_port() {
    local proto="$1" port="$2"
    if command -v ufw >/dev/null 2>&1; then
        sudo ufw allow "$port/$proto"
    elif command -v firewall-cmd >/dev/null 2>&1; then
        sudo firewall-cmd --permanent --add-port="$port/$proto"
        sudo firewall-cmd --reload
    elif command -v iptables >/dev/null 2>&1; then
        sudo iptables -C INPUT -p "$proto" --dport "$port" -j ACCEPT 2>/dev/null \
            || sudo iptables -I INPUT -p "$proto" --dport "$port" -j ACCEPT
    else
        echo "  No firewall tool found (ufw/firewalld/iptables). Open port $port/$proto manually if your firewall needs it."
    fi
}

echo
echo "[2/2] Opening firewall ports for Lan-Share..."
echo "  - 8501/tcp  (frame relay)"
echo "  - 8502/udp  (LAN discovery)"
open_port tcp 8501
open_port udp 8502
echo "  firewall rules added (if a firewall tool was found)."

echo
echo "== Done. Run these directly from this folder: =="
echo
echo "  # one machine hosts the relay (reachable on 0.0.0.0)"
echo "  python3 -m server.main --host 0.0.0.0 --port 8501"
echo
echo "  # every other machine, with the relay machine's IP:"
echo "  python3 -m client.desktop --server <SERVER_IP> --port 8501"
echo "  python3 -m client.manager --open            # browser GUI, no GTK needed"
echo "  python3 -m client.main share --server <SERVER_IP> --stream desk"
echo "  python3 -m client.main view  --server <SERVER_IP> --stream desk"
echo
echo "  # check a client can reach the relay:"
echo "  python3 -c \"import socket; socket.create_connection(('<SERVER_IP>',8501)); print('relay reachable')\""
echo
echo "  # diagnose connectivity between two machines:"
echo "  ./scripts/diagnose_lan.sh <SERVER_IP>"
