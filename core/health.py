"""Relay / stream health checks.

Uses the PING/PONG protocol messages to tell apart four situations:

    A. unreachable    - the relay TCP port does not answer at all
    B. stream-missing - the relay is up but the requested stream is not shared
    C. no-frames      - the stream exists but no frame arrives within the window
    D. active         - frames are flowing

Only the Python standard library.
"""

import socket
import time

from core import protocol

logger = None


def _log():
    global logger
    if logger is None:
        import logging
        logger = logging.getLogger("lan-share.health")
    return logger


def _send(sock, header):
    protocol.send_message(sock, header)


def _recv_timeout(sock, timeout):
    """Receive one message, returning (header, payload) or raising TimeoutError."""
    sock.settimeout(timeout)
    return protocol.recv_message(sock)


def ping_relay(server, port, timeout=3.0):
    """Send PING and wait for PONG. Returns True if the relay answered."""
    try:
        sock = socket.create_connection((server, port), timeout=min(timeout, 3.0))
    except OSError:
        return False
    try:
        sock.settimeout(timeout)
        _send(sock, {"type": protocol.PING, "id": "health"})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            header, _ = protocol.recv_message(sock)
            if header.get("type") == protocol.PONG and header.get("id") == "health":
                return True
    except (OSError, protocol.ProtocolError, socket.timeout):
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return False


def check(server, port, stream=None, connect_timeout=3.0, frame_window=3.0):
    """Return a status dict describing the relay / stream health.

    ``stream`` may be None to only verify the relay is reachable. The result
    dict always contains ``status`` with one of:

        unreachable / stream-missing / no-frames / active
    """
    result = {
        "server": server,
        "port": port,
        "stream": stream,
        "status": "unreachable",
        "relay_ping": False,
        "message": "relay is not reachable at %s:%s" % (server, port),
    }
    try:
        sock = socket.create_connection((server, port), timeout=connect_timeout)
    except OSError as exc:
        result["message"] = "cannot connect to relay at %s:%s (%s)" % (server, port, exc)
        return result

    try:
        sock.settimeout(connect_timeout)
        _send(sock, {"type": protocol.PING, "id": "health"})
        pong = False
        deadline = time.monotonic() + connect_timeout
        while time.monotonic() < deadline:
            header, _ = protocol.recv_message(sock)
            if header.get("type") == protocol.PONG and header.get("id") == "health":
                pong = True
                break
        result["relay_ping"] = pong
        result["status"] = "active" if stream is None else "stream-missing"
        result["message"] = "relay is reachable at %s:%s" % (server, port)
        if stream is None:
            return result

        _send(sock, {"type": protocol.STREAM_LIST})
        header, _ = _recv_timeout(sock, connect_timeout)
        streams = header.get("streams", []) if header.get("type") == protocol.STREAM_LIST_RESP else []
        if stream not in streams:
            result["status"] = "stream-missing"
            result["message"] = "relay is up but stream '%s' is not shared" % stream
            return result

        result["status"] = "no-frames"
        result["message"] = "stream '%s' is shared but no frames arrive" % stream
        _send(sock, {"type": protocol.SUBSCRIBE, "stream": stream})
        header, _ = _recv_timeout(sock, connect_timeout)
        if header.get("type") == protocol.ERROR:
            result["status"] = "stream-missing"
            result["message"] = "relay refused stream '%s': %s" % (
                stream, header.get("reason", "unknown")
            )
            return result
        deadline = time.monotonic() + frame_window
        while time.monotonic() < deadline:
            header, payload = protocol.recv_message(sock)
            if header.get("type") == protocol.FRAME and payload:
                result["status"] = "active"
                result["message"] = "stream '%s' is live" % stream
                return result
    except socket.timeout:
        pass
    except (OSError, protocol.ProtocolError) as exc:
        result["message"] = "connection to relay failed: %s" % exc
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return result


def check_stream(server, port, stream, frame_window=3.0):
    """Shorthand for :func:`check` when a specific stream must be verified."""
    return check(server, port, stream=stream, frame_window=frame_window)
