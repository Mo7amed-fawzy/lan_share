"""TCP framing and message helpers for Lan-Share.

Wire format for every message:

    [ 4-byte big-endian length ][ JSON header bytes ][ optional payload ]

The header is a JSON object. Frame payloads are raw JPEG bytes whose byte
count is stored inside the header under the "size" key.
"""

import json
import socket
import struct

HEADER_LEN = struct.calcsize(">I")

# Message types
SHARER_HELLO = "sharer_hello"
SHARER_REGISTERED = "sharer_registered"
STREAM_LIST = "stream_list"
STREAM_LIST_RESP = "stream_list_resp"
SUBSCRIBE = "subscribe"
SUBSCRIBED = "subscribed"
STREAM_STARTED = "stream_started"
STREAM_STOPPED = "stream_stopped"
FRAME = "frame"
PING = "ping"
PONG = "pong"
BYE = "bye"
ERROR = "error"

# ERROR "reason" values
UNKNOWN_STREAM = "unknown_stream"
NOT_SHARER = "not_sharer"
INVALID_MESSAGE = "invalid_message"


class ProtocolError(Exception):
    """Raised when a message cannot be decoded."""


def send_message(sock, header, payload=None):
    """Serialize ``header`` (dict) and optional ``payload`` (bytes) to ``sock``."""
    if payload is not None:
        header = dict(header)
        header["size"] = len(payload)
    data = json.dumps(header).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)))
    sock.sendall(data)
    if payload:
        sock.sendall(payload)


def recv_exact(sock, n):
    """Read exactly ``n`` bytes from ``sock`` or raise ProtocolError."""
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ProtocolError("connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_message(sock):
    """Receive one message. Returns (header_dict, payload_or_None)."""
    raw_len = recv_exact(sock, HEADER_LEN)
    header_size = struct.unpack(">I", raw_len)[0]
    if header_size <= 0 or header_size > 16 * 1024 * 1024:
        raise ProtocolError("invalid header size")
    header_bytes = recv_exact(sock, header_size)
    try:
        header = json.loads(header_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError("invalid header JSON") from exc
    payload = None
    if header.get("size"):
        payload = recv_exact(sock, int(header["size"]))
    return header, payload
