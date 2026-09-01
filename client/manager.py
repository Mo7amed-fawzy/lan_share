#!/usr/bin/env python3
"""Lan-Share client GUI manager.

A local browser-based control panel for the client side. Run it on the
machine that wants to share / watch screens, then open http://127.0.0.1:8701

    python3 -m client.manager --port 8701

Features:
    - configure the relay server (IP + port)
    - start / stop sharing this screen
    - list streams currently shared on the relay
    - watch any stream directly in the browser (MJPEG, no tkinter needed)

Uses only the Python standard library.
"""

import argparse
import json
import logging
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from core import discovery, health, protocol
from core.capture import ScreenCapture

logger = logging.getLogger("lan-share.manager")

WEB_DIR = __import__("pathlib").Path(__file__).resolve().parent / "web"

REGISTER_TIMEOUT = 5.0
SUBSCRIBE_TIMEOUT = 5.0
NO_FRAME_TIMEOUT = 10.0


class ShareManager:
    """Holds the client-side state and drives the sharing worker."""

    def __init__(self):
        self.relay_server = "127.0.0.1"
        self.relay_port = 8501
        self.relay_state = "manual"
        self.discovered_via = None
        self._share_thread = None
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self.sharing = False
        self.stream = ""
        self.last_error = ""

    # -- sharing ---------------------------------------------------------

    def start_share(self, server, port, stream, name, fps, quality, region=None):
        with self._state_lock:
            self.stop_share_locked()
            self.relay_server, self.relay_port = server, port
            self.stream = stream
            self.sharing = True
            self.last_error = ""
            self._stop_event = threading.Event()
            thread = threading.Thread(
                target=self._share_worker,
                args=(server, port, stream, name, fps, quality, region),
                daemon=True,
            )
            self._share_thread = thread
        thread.start()
        logger.info("sharing started: %s on %s:%s", stream, server, port)

    def stop_share(self):
        with self._state_lock:
            self.stop_share_locked()
        logger.info("sharing stopped")

    def stop_share_locked(self):
        self.sharing = False
        self._stop_event.set()
        if self._share_thread is not None:
            self._share_thread.join(timeout=3)
            self._share_thread = None

    def _share_worker(self, server, port, stream, name, fps, quality, region):
        capture = ScreenCapture(region=region, quality=quality)
        sock = None
        try:
            sock = socket.create_connection((server, port), timeout=5)
            protocol.send_message(
                sock, {"type": protocol.SHARER_HELLO, "stream": stream, "host": name}
            )
            try:
                self._wait_ack(sock, protocol.SHARER_REGISTERED, REGISTER_TIMEOUT)
            except RuntimeError as exc:
                raise RuntimeError("share failed: %s" % exc)
            logger.info("registered as sharer for '%s' on %s:%s", stream, server, port)
            sock.settimeout(None)
            seq = 0
            while not self._stop_event.is_set():
                start = time.monotonic()
                payload = capture.grab_jpeg()
                protocol.send_message(
                    sock,
                    {"type": protocol.FRAME, "stream": stream, "seq": seq},
                    payload,
                )
                seq += 1
                elapsed = time.monotonic() - start
                self._stop_event.wait(max(0.0, 1.0 / fps - elapsed))
        except (OSError, protocol.ProtocolError, RuntimeError) as exc:
            with self._state_lock:
                self.last_error = "share failed: %s" % exc
                self.sharing = False
            logger.error("share worker error: %s", exc)
        finally:
            if sock is not None:
                try:
                    protocol.send_message(sock, {"type": protocol.BYE})
                except OSError:
                    pass
                sock.close()

    @staticmethod
    def _wait_ack(sock, expected_type, timeout):
        sock.settimeout(timeout)
        try:
            while True:
                header, _ = protocol.recv_message(sock)
                if header.get("type") == expected_type:
                    return header
                if header.get("type") == protocol.ERROR:
                    raise RuntimeError(header.get("reason", "server refused the request"))
        except socket.timeout:
            raise RuntimeError("no %s reply from the relay within %.0fs" % (expected_type, timeout))

    # -- relay queries ---------------------------------------------------

    def list_streams(self, server, port):
        """Return the list of stream names shared on the relay."""
        sock = socket.create_connection((server, port), timeout=5)
        try:
            sock.settimeout(5)
            protocol.send_message(sock, {"type": protocol.STREAM_LIST})
            header, _ = protocol.recv_message(sock)
            if header.get("type") != protocol.STREAM_LIST_RESP:
                raise RuntimeError("unexpected reply: %s" % header)
            return header.get("streams", [])
        finally:
            sock.close()

    def relay_health(self):
        """Return the current relay state used by the web UI.

        States: connected / unreachable / discovery-failed / manual.
        """
        if not self.relay_server:
            return "manual"
        if health.ping_relay(self.relay_server, self.relay_port, timeout=1.0):
            return "connected"
        return "unreachable"


class ManagerHTTPHandler(BaseHTTPRequestHandler):
    server_version = "LanShareManager/1.0"

    manager = None  # set by the CLI runner

    # -- helpers ---------------------------------------------------------

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type="text/html; charset=utf-8"):
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404, "not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _params(self):
        return parse_qs(urlparse(self.path).query)

    def _relay_from_params(self, params):
        server = (params.get("server") or [self.manager.relay_server])[0]
        try:
            port = int((params.get("port") or [self.manager.relay_port])[0])
        except ValueError:
            port = self.manager.relay_port
        return server, port

    # -- routes ----------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = self._params()

        if path in ("/", "/index.html"):
            self._send_file(WEB_DIR / "index.html")
        elif path == "/api/status":
            self._send_json({
                "sharing": self.manager.sharing,
                "stream": self.manager.stream,
                "relay_server": self.manager.relay_server,
                "relay_port": self.manager.relay_port,
                "relay_state": self.manager.relay_health(),
                "discovered_via": self.manager.discovered_via,
                "error": self.manager.last_error,
            })
        elif path == "/api/streams":
            server, port = self._relay_from_params(params)
            try:
                streams = self.manager.list_streams(server, port)
                self._send_json({"streams": streams, "server": server, "port": port,
                                 "relay_state": "connected"})
            except (OSError, protocol.ProtocolError, RuntimeError) as exc:
                self._send_json({"streams": [], "error": str(exc), "relay_state": "unreachable",
                                 "server": server, "port": port}, status=200)
        elif path.startswith("/watch/"):
            stream = unquote(path[len("/watch/"):])
            if not stream:
                self.send_error(400, "missing stream")
                return
            self._send_file(WEB_DIR / "watch.html")
        elif path.startswith("/stream/"):
            stream = unquote(path[len("/stream/"):])
            server, port = self._relay_from_params(params)
            self._stream_mjpeg(stream, server, port)
        else:
            self.send_error(404, "not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        if parsed.path == "/api/share/start":
            server = body.get("server") or self.manager.relay_server
            port = int(body.get("port") or self.manager.relay_port)
            stream = body.get("stream") or socket.gethostname()
            if not server:
                self._send_json({"ok": False, "sharing": False,
                                 "error": "manual server required: enter the relay IP"}, status=200)
                return
            fps = float(body.get("fps") or 10.0)
            quality = int(body.get("quality") or 80)
            self.manager.start_share(server, port, stream, body.get("name") or stream,
                                     fps, quality)
            self._send_json({"ok": True, "sharing": self.manager.sharing})
        elif parsed.path == "/api/share/stop":
            self.manager.stop_share()
            self._send_json({"ok": True, "sharing": False})
        else:
            self.send_error(404, "not found")

    # -- MJPEG streaming -------------------------------------------------

    def _stream_mjpeg(self, stream, server, port):
        """Relay one stream from the Lan-Share server to the browser as MJPEG."""
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        sock = None
        try:
            sock = socket.create_connection((server, port), timeout=5)
            protocol.send_message(sock, {"type": protocol.SUBSCRIBE, "stream": stream})
            sock.settimeout(SUBSCRIBE_TIMEOUT)
            try:
                while True:
                    header, _ = protocol.recv_message(sock)
                    if header.get("type") == protocol.SUBSCRIBED:
                        break
                    if header.get("type") == protocol.ERROR:
                        return
            except socket.timeout:
                return
            sock.settimeout(NO_FRAME_TIMEOUT)
            while True:
                header, payload = protocol.recv_message(sock)
                if header.get("type") == protocol.ERROR or not payload:
                    break
                if header.get("type") != protocol.FRAME:
                    continue
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(b"Content-Length: %d\r\n\r\n" % len(payload))
                self.wfile.write(payload)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except (OSError, protocol.ProtocolError, BrokenPipeError, socket.timeout):
            pass
        finally:
            if sock is not None:
                sock.close()

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.client_address[0], fmt % args)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lan-Share client GUI manager")
    parser.add_argument("--host", default="127.0.0.1", help="bind address for the web UI")
    parser.add_argument("--port", type=int, default=8701, help="web UI port")
    parser.add_argument("--open", action="store_true", help="open the browser automatically")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    ManagerHTTPHandler.manager = ShareManager()
    info = discovery.discover_detail(timeout=2.0)
    if info:
        ManagerHTTPHandler.manager.relay_server = info["ip"]
        ManagerHTTPHandler.manager.discovered_via = info["via"]
        ManagerHTTPHandler.manager.relay_state = "connected"
        logger.info("relay discovered at %s:%s (%s)", info["ip"], info["port"], info["via"])
    else:
        ManagerHTTPHandler.manager.relay_server = ""
        ManagerHTTPHandler.manager.relay_state = "manual"
        logger.info("no relay discovered on the LAN - manual server required")
    httpd = ThreadingHTTPServer((args.host, args.port), ManagerHTTPHandler)
    logger.info("Lan-Share GUI manager at http://%s:%s", args.host, args.port)
    if args.open:
        webbrowser.open("http://%s:%s" % (args.host, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ManagerHTTPHandler.manager.stop_share()
        httpd.server_close()


if __name__ == "__main__":
    main()
