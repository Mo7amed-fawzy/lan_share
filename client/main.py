#!/usr/bin/env python3
"""Lan-Share client: share your screen or watch a shared screen.

Share mode:

    python3 -m client.main share --server 192.168.1.50 --port 8501 --stream desk

View mode:

    python3 -m client.main view --server 192.168.1.50 --port 8501 --stream desk

Health mode:

    python3 -m client.main health --server 192.168.1.50 --stream desk
"""

import argparse
import logging
import socket
import sys
import threading
import time
from core import health, protocol
from core.capture import ScreenCapture
from core.player import create_player

logger = logging.getLogger("lan-share.client")

REGISTER_TIMEOUT = 5.0
SUBSCRIBE_TIMEOUT = 5.0
NO_FRAME_TIMEOUT = 15.0


def _connect(server, port):
    sock = socket.create_connection((server, port), timeout=5)
    sock.settimeout(None)
    logger.info("connected to %s:%s", server, port)
    return sock


def _wait_ack(sock, expected_type, timeout):
    """Wait for a message of ``expected_type``; return its header.

    Raises RuntimeError on ERROR or timeout.
    """
    sock.settimeout(timeout)
    try:
        while True:
            header, _ = protocol.recv_message(sock)
            if header.get("type") == expected_type:
                return header
            if header.get("type") == protocol.ERROR:
                raise RuntimeError(header.get("reason", "server refused the request"))
    except socket.timeout:
        raise RuntimeError("no %s from the server within %.0fs" % (expected_type, timeout))


def run_share(args):
    sock = _connect(args.server, args.port)
    capture = ScreenCapture(region=args.region, scale=args.scale, quality=args.quality)
    try:
        protocol.send_message(
            sock,
            {"type": protocol.SHARER_HELLO, "stream": args.stream, "host": args.name},
        )
        try:
            _wait_ack(sock, protocol.SHARER_REGISTERED, REGISTER_TIMEOUT)
        except RuntimeError as exc:
            logger.error("could not start sharing: %s", exc)
            sys.exit(1)
        logger.info("registered as sharer for '%s' at %.1f fps", args.stream, args.fps)
        sock.settimeout(None)
        seq = 0
        while True:
            start = time.monotonic()
            payload = capture.grab_jpeg()
            protocol.send_message(
                sock,
                {"type": protocol.FRAME, "stream": args.stream, "seq": seq},
                payload,
            )
            seq += 1
            elapsed = time.monotonic() - start
            time.sleep(max(0.0, 1.0 / args.fps - elapsed))
    except KeyboardInterrupt:
        pass
    except (ConnectionResetError, BrokenPipeError, OSError, protocol.ProtocolError) as exc:
        logger.error(
            "lost connection to the server (%s). Is the server running on %s:%s? "
            "Try: ./scripts/run_server.sh",
            exc,
            args.server,
            args.port,
        )
        sys.exit(1)
    finally:
        try:
            protocol.send_message(sock, {"type": protocol.BYE})
        except OSError:
            pass
        sock.close()
        logger.info("screen sharing stopped")


def run_view(args):
    sock = _connect(args.server, args.port)
    protocol.send_message(sock, {"type": protocol.STREAM_LIST})
    header, _ = protocol.recv_message(sock)
    streams = header.get("streams", [])
    if not streams:
        print("No active streams on the server.")
        sock.close()
        return 1
    print("Available streams:", ", ".join(streams))
    stream = args.stream
    if not stream:
        stream = streams[0]
        print("Using stream:", stream)

    protocol.send_message(sock, {"type": protocol.SUBSCRIBE, "stream": stream})
    try:
        _wait_ack(sock, protocol.SUBSCRIBED, SUBSCRIBE_TIMEOUT)
    except RuntimeError as exc:
        print("Cannot watch '%s': %s" % (stream, exc))
        sock.close()
        return 1
    print("Watching stream:", stream)
    sock.settimeout(NO_FRAME_TIMEOUT)

    stop = threading.Event()

    def pump():
        while not stop.is_set():
            try:
                header, payload = protocol.recv_message(sock)
            except socket.timeout:
                print("No frames received from '%s' for %.0fs - the sharer may have stopped."
                      % (stream, NO_FRAME_TIMEOUT))
                stop.set()
                break
            except (OSError, protocol.ProtocolError):
                break
            if header.get("type") == protocol.FRAME and payload:
                player.show(payload)

    player = create_player(title="Lan-Share - %s" % stream, on_close=stop.set)
    threading.Thread(target=pump, daemon=True).start()
    player.start()
    stop.set()
    sock.close()
    logger.info("viewer closed")
    return 0


def run_health(args):
    result = health.check(
        args.server,
        args.port,
        stream=args.stream,
        frame_window=args.frame_window,
    )
    print("relay     : %s:%s" % (result["server"], result["port"]))
    print("relay ping: %s" % ("ok" if result["relay_ping"] else "no reply"))
    if args.stream:
        print("stream    : %s" % args.stream)
    print("status    : %s" % result["status"])
    print("message   : %s" % result["message"])
    return 0 if result["status"] == "active" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lan-Share client")
    sub = parser.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--server", default=None, help="server IP on the LAN (default: auto-discover)")
    common.add_argument("--port", type=int, default=8501)
    common.add_argument("--log-level", default="INFO")

    share = sub.add_parser("share", parents=[common], help="share your screen")
    share.add_argument("--stream", default=socket.gethostname(), help="stream name")
    share.add_argument("--name", default=socket.gethostname(), help="human readable host name")
    share.add_argument("--fps", type=float, default=10.0)
    share.add_argument("--quality", type=int, default=80, help="JPEG quality 1-100")
    share.add_argument("--scale", type=float, default=0.5, help="downscale factor for the capture")
    share.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"),
                       help="capture region x y width height")

    view = sub.add_parser("view", parents=[common], help="watch a shared screen")
    view.add_argument("--stream", default=None, help="stream name (defaults to first)")

    check = sub.add_parser("health", parents=[common], help="check relay / stream health")
    check.add_argument("--stream", default=None, help="stream name to verify (optional)")
    check.add_argument("--frame-window", type=float, default=3.0,
                       help="seconds to wait for a frame before declaring no-frames")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if not args.server:
        from core import discovery
        found = discovery.discover(timeout=2.0)
        if found:
            args.server = found[0]
            logger.info("relay discovered at %s:%s", *found)
        else:
            print("No relay discovered on the LAN. Pass --server <IP> explicitly, "
                  "e.g. python3 -m client.main %s --server 192.168.1.50"
                  % args.mode)
            sys.exit(1)

    if args.mode == "share":
        run_share(args)
    elif args.mode == "view":
        sys.exit(run_view(args))
    else:
        sys.exit(run_health(args))


if __name__ == "__main__":
    main()
