#!/usr/bin/env python3
"""Lan-Share server: relays shared screens to subscribers on the LAN.

Run from the project root:

    python3 -m server.main --host 0.0.0.0 --port 8501
"""

import argparse
import errno
import logging
import queue
import socket
import threading
import time

from core import discovery, protocol

logger = logging.getLogger("lan-share.server")


class Client:
    """A connected peer tracked by the server."""

    _next_id = 0
    _id_lock = threading.Lock()

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.stream = None
        self.is_sharer = False
        self.generation = 0
        with Client._id_lock:
            Client._next_id += 1
            self.client_id = Client._next_id
        self._queue = queue.Queue(maxsize=60)
        self._closed = False

    def enqueue(self, header, payload=None):
        try:
            self._queue.put_nowait((False, header, payload))
        except queue.Full:
            pass

    def enqueue_latest(self, header, payload=None):
        """Queue the newest frame, dropping any stale frames ahead of it.

        Control messages (is_frame=False) are preserved. Keeps each viewer on
        the latest frame so a slow viewer never makes the relay accumulate a
        backlog and never blocks other viewers.
        """
        kept = []
        try:
            while True:
                item = self._queue.get_nowait()
                if not item[0]:
                    kept.append(item)
        except queue.Empty:
            pass
        for item in kept[:20]:
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                break
        try:
            self._queue.put_nowait((True, header, payload))
        except queue.Full:
            pass

    def send(self, header, payload=None):
        """Queue a message for the writer thread.

        Everything a client receives goes through this single writer, so the
        socket is never written by two threads at once (which would corrupt
        the framed messages). Ordering is preserved by the FIFO queue.
        """
        try:
            self._queue.put((False, header, payload), timeout=2.0)
        except queue.Full:
            pass

    def writer_loop(self):
        try:
            while not self._closed:
                try:
                    _, header, payload = self._queue.get(timeout=1)
                except queue.Empty:
                    continue
                protocol.send_message(self.sock, header, payload)
        except (OSError, protocol.ProtocolError):
            pass
        finally:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.close()
        except OSError:
            pass


class ShareServer:
    """TCP relay hub: sharers publish frames, viewers subscribe."""

    def __init__(self, host="0.0.0.0", port=8501):
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._clients = set()             # all connected peers
        self._sharers = {}                # stream -> Client
        self._viewers = {}                # stream -> set(Client)
        self._last_frame = {}             # stream -> (header, payload)
        self._stream_gen = {}             # stream -> generation counter
        self._stream_started = {}         # stream -> monotonic time share began
        self._last_frame_time = {}        # stream -> monotonic time of newest frame
        self._stream_meta = {}            # stream -> {host, addr, fps, size}
        self._control_owner = {}          # stream -> client_id of viewer with control
        self._running = True
        self._server_sock = None
        self._discovery = discovery.DiscoveryResponder(relay_port=port)

    # -- lifecycle --------------------------------------------------------

    def start(self):
        self._discovery.start()
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._server_sock.bind((self.host, self.port))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                logger.error(
                    "Port %s already in use - a Lan-Share server (or another "
                    "process) is already bound to it.",
                    self.port,
                )
                logger.error(
                    "Find and stop it, e.g. on Linux: "
                    "ss -ltnp 'sport = :%s'  or  pkill -f 'server.main'",
                    self.port,
                )
            else:
                logger.error("Cannot bind to %s:%s: %s", self.host, self.port, exc)
            return
        self._server_sock.listen(64)
        logger.info("Lan-Share server listening on %s:%s", self.host, self.port)
        while self._running:
            try:
                sock, addr = self._server_sock.accept()
            except OSError:
                break
            logger.info("client connected: %s", addr)
            threading.Thread(target=self._handle_client, args=(sock, addr), daemon=True).start()

    def _handle_client(self, sock, addr):
        client = Client(sock, addr)
        with self._lock:
            self._clients.add(client)
        threading.Thread(target=client.writer_loop, daemon=True).start()
        try:
            while True:
                header, payload = protocol.recv_message(sock)
                try:
                    self._dispatch(client, header, payload)
                except Exception:
                    logger.exception("error handling message from %s", addr)
                    break
        except (OSError, protocol.ProtocolError):
            pass
        finally:
            self._unregister(client)
            with self._lock:
                self._clients.discard(client)
            client.close()
            logger.info("client left: %s", addr)

    def _broadcast(self, header, exclude=None):
        """Queue a message to every connected peer except ``exclude``.

        The caller must hold ``self._lock`` (the lock is not reentrant).
        """
        targets = [c for c in self._clients if c is not exclude]
        for client in targets:
            client.enqueue(header)

    # -- message dispatch -------------------------------------------------

    def _dispatch(self, client, header, payload):
        msg_type = header.get("type")
        if msg_type == protocol.SHARER_HELLO:
            self._register_sharer(client, header)
        elif msg_type == protocol.STREAM_LIST:
            with self._lock:
                streams = list(self._sharers)
                now = time.monotonic()
                viewer_counts = {}
                last_frame_age = {}
                uptime = {}
                hosts = {}
                addresses = {}
                fps = {}
                sizes = {}
                for s in streams:
                    viewer_counts[s] = len(self._viewers.get(s, ()))
                    started = self._stream_started.get(s)
                    uptime[s] = round(now - started, 1) if started is not None else None
                    last = self._last_frame_time.get(s)
                    last_frame_age[s] = round(now - last, 1) if last is not None else None
                    meta = self._stream_meta.get(s) or {}
                    hosts[s] = meta.get("host") or s
                    addresses[s] = meta.get("addr") or ""
                    fps[s] = meta.get("fps")
                    sizes[s] = meta.get("size")
            client.send({
                "type": protocol.STREAM_LIST_RESP,
                "streams": streams,
                "viewer_counts": viewer_counts,
                "last_frame_age": last_frame_age,
                "uptime": uptime,
                "hosts": hosts,
                "addresses": addresses,
                "fps": fps,
                "sizes": sizes,
            })
        elif msg_type == protocol.SUBSCRIBE:
            self._subscribe(client, header)
        elif msg_type == protocol.FRAME:
            self._on_frame(client, header, payload)
        elif msg_type == protocol.PING:
            client.send({"type": protocol.PONG, "id": header.get("id")})
        elif msg_type == protocol.BYE:
            self._unregister(client)
            client.close()
        elif msg_type in (
            protocol.CONTROL_REQUEST, protocol.CONTROL_GRANTED,
            protocol.CONTROL_DENIED, protocol.CONTROL_REVOKED,
            protocol.CONTROL_INPUT,
        ):
            self._route_control(client, header)

    def _register_sharer(self, client, header):
        stream = header.get("stream")
        if not stream:
            client.send({"type": protocol.ERROR, "reason": protocol.INVALID_MESSAGE})
            return
        with self._lock:
            old = self._sharers.get(stream)
            replaced = old is not None and old is not client
            if replaced:
                old.close()
                self._unregister_locked(old, broadcast=False)
            generation = self._stream_gen.get(stream, 0) + 1
            self._stream_gen[stream] = generation
            self._control_owner.pop(stream, None)
            client.stream = stream
            client.is_sharer = True
            client.generation = generation
            self._sharers[stream] = client
            self._stream_started[stream] = time.monotonic()
            self._viewers.setdefault(stream, set())
            width = header.get("width") or 1600
            height = header.get("height") or 900
            self._stream_meta[stream] = {
                "host": header.get("host") or stream,
                "addr": client.addr[0],
                "fps": header.get("fps"),
                "size": [width, height],
            }
        client.send({"type": protocol.SHARER_REGISTERED, "stream": stream})
        if replaced:
            logger.info("sharer replaced for %s: %s", stream, client.addr)
        logger.info("sharer registered: %s (%s)", stream, client.addr)
        with self._lock:
            self._broadcast(
                {"type": protocol.STREAM_STARTED, "stream": stream},
                exclude=client,
            )

    def _subscribe(self, client, header):
        stream = header.get("stream")
        with self._lock:
            sharer = self._sharers.get(stream)
            if sharer is None:
                client.send(
                    {"type": protocol.ERROR, "reason": protocol.UNKNOWN_STREAM,
                     "stream": stream}
                )
                return
            self._viewers.setdefault(stream, set()).add(client)
            client.stream = stream
            client.is_sharer = False
            client.generation = self._stream_gen.get(stream, 0)
            last = self._last_frame.get(stream)
            viewer_count = len(self._viewers[stream])
        client.send({"type": protocol.SUBSCRIBED, "stream": stream})
        if last:
            client.enqueue_latest(*last)
        logger.info(
            "viewer subscribed to %s (%s) - %d viewer(s)",
            stream, client.addr, viewer_count,
        )

    def _on_frame(self, client, header, payload):
        stream = client.stream
        if not client.is_sharer or stream is None:
            return
        with self._lock:
            if self._sharers.get(stream) is not client:
                return
            if self._stream_gen.get(stream, 0) != client.generation:
                logger.warning(
                    "ignoring frame from stale sharer of %s (%s)",
                    stream, client.addr,
                )
                return
            frame = (header, payload)
            first = stream not in self._last_frame
            self._last_frame[stream] = frame
            self._last_frame_time[stream] = time.monotonic()
            viewers = list(self._viewers.get(stream, ()))
        if first:
            logger.info("first frame received for %s (%s)", stream, client.addr)
        for viewer in viewers:
            viewer.enqueue_latest(*frame)

    def _route_control(self, client, header):
        """Route control messages between viewer and sharer."""
        msg_type = header.get("type")
        stream = client.stream
        if stream is None:
            return
        with self._lock:
            if msg_type == protocol.CONTROL_REQUEST:
                sharer = self._sharers.get(stream)
                if sharer is None:
                    client.send({"type": protocol.CONTROL_DENIED, "reason": "no_sharer"})
                    return
                self._control_owner[stream] = client.client_id
                sharer.send({
                    "type": protocol.CONTROL_REQUEST,
                    "viewer_id": client.client_id,
                    "viewer_host": client.addr[0],
                    "stream": stream,
                })
                logger.info(
                    "control request from viewer %d (%s) for %s",
                    client.client_id, client.addr, stream,
                )
            elif msg_type in (protocol.CONTROL_GRANTED, protocol.CONTROL_DENIED):
                owner_id = self._control_owner.get(stream)
                if msg_type == protocol.CONTROL_DENIED:
                    self._control_owner.pop(stream, None)
                for v in self._viewers.get(stream, ()):
                    if v.client_id == owner_id:
                        v.send({
                            "type": msg_type,
                            "stream": stream,
                        })
                        break
            elif msg_type == protocol.CONTROL_REVOKED:
                self._control_owner.pop(stream, None)
                for v in self._viewers.get(stream, ()):
                    v.send({"type": protocol.CONTROL_REVOKED, "stream": stream})
                sharer = self._sharers.get(stream)
                if sharer:
                    sharer.send({"type": protocol.CONTROL_REVOKED, "stream": stream})
                logger.info("control revoked for %s", stream)
            elif msg_type == protocol.CONTROL_INPUT:
                owner_id = self._control_owner.get(stream)
                if owner_id != client.client_id:
                    return
                sharer = self._sharers.get(stream)
                if sharer:
                    sharer.send(header)

    def _unregister(self, client):
        with self._lock:
            self._unregister_locked(client)

    def _unregister_locked(self, client, broadcast=True):
        stream = client.stream
        if stream is None:
            return
        was_sharer = self._sharers.get(stream) is client
        if was_sharer:
            del self._sharers[stream]
            self._last_frame.pop(stream, None)
            self._last_frame_time.pop(stream, None)
            self._stream_gen.pop(stream, None)
            self._stream_started.pop(stream, None)
            self._stream_meta.pop(stream, None)
            self._control_owner.pop(stream, None)
            logger.info("sharer left: %s (%s)", stream, client.addr)
        viewers = self._viewers.get(stream)
        if viewers and client in viewers:
            viewers.discard(client)
            if self._control_owner.get(stream) == client.client_id:
                self._control_owner.pop(stream, None)
                sharer = self._sharers.get(stream)
                if sharer:
                    sharer.send({"type": protocol.CONTROL_REVOKED, "stream": stream})
        if viewers is not None and not viewers:
            self._viewers.pop(stream, None)
        client.stream = None
        client.is_sharer = False
        if was_sharer and broadcast:
            self._broadcast(
                {"type": protocol.STREAM_STOPPED, "stream": stream},
                exclude=client,
            )

    # -- stop -------------------------------------------------------------

    def stop(self):
        self._running = False
        self._discovery.stop()
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        with self._lock:
            for client in list(self._clients):
                client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lan-Share server (stream relay)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    server = ShareServer(host=args.host, port=args.port)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
