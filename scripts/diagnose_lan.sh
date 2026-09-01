#!/usr/bin/env bash
# Diagnose Lan-Share connectivity to a relay host on the LAN.
#
#     ./scripts/diagnose_lan.sh <SERVER_IP> [STREAM]
#
# Checks, in order:
#   1. the host answers ping
#   2. the relay TCP port (8501) is open and answers PING/PONG
#   3. the discovery UDP port (8502) answers a unicast probe
#   4. the stream health (if a stream name is given), or the relay health
#
# Exits 0 only when every applicable check passes.
set -uo pipefail

cd "$(dirname "$0")/.."

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <SERVER_IP> [STREAM]" >&2
    echo "Example: $0 192.168.1.50 desk" >&2
    exit 2
fi
SERVER_IP="$1"
STREAM="${2:-}"
PORT_TCP=8501
PORT_UDP=8502
FAILED=0

step() { echo; echo "== $1 =="; }
ok()   { echo "  OK: $1"; }
bad()  { echo "  FAIL: $1"; FAILED=1; }

step "1/4 - Host reachable (ping)"
if ping -c 2 -W 2 "$SERVER_IP" >/dev/null 2>&1; then
    ok "$SERVER_IP answers ping"
else
    bad "$SERVER_IP does not answer ping (is it on the same LAN?)"
fi

step "2/4 - Relay TCP port ${PORT_TCP} (PING/PONG)"
if python3 - "$SERVER_IP" "$PORT_TCP" <<'PY'
import sys
from core import health
r = health.ping_relay(sys.argv[1], int(sys.argv[2]), timeout=3.0)
sys.exit(0 if r else 1)
PY
then
    ok "relay at $SERVER_IP:$PORT_TCP is alive and answers PING/PONG"
else
    bad "relay at $SERVER_IP:$PORT_TCP is not reachable"
    echo "      check: is the relay running? ./scripts/run_server.sh"
    echo "      check: firewall allows ${PORT_TCP}/tcp on the relay PC? ./scripts/setup_lan.sh"
fi

step "3/4 - Discovery UDP port ${PORT_UDP}"
if python3 - "$SERVER_IP" "$PORT_UDP" <<'PY'
import sys
from core import discovery
r = discovery.probe(sys.argv[1], int(sys.argv[2]), timeout=3.0)
sys.exit(0 if r else 1)
PY
then
    ok "discovery at $SERVER_IP:$PORT_UDP answers (relay port advertised)"
else
    bad "discovery at $SERVER_IP:$PORT_UDP did not answer"
    echo "      check: firewall allows ${PORT_UDP}/udp on the relay PC? ./scripts/setup_lan.sh"
fi

if [ -n "$STREAM" ]; then
    step "4/4 - Stream health ('$STREAM')"
else
    step "4/4 - Relay health"
fi
if python3 -m client.main health --server "$SERVER_IP" --port "$PORT_TCP" \
        ${STREAM:+--stream "$STREAM"}; then
    ok "health check passed"
else
    bad "health check failed"
fi

echo
if [ "$FAILED" -eq 0 ]; then
    echo "All checks passed - Lan-Share is reachable at $SERVER_IP."
    exit 0
else
    echo "Some checks failed. Fix the failures above, then re-run this script."
    exit 1
fi
