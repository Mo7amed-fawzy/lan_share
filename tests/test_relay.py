#!/usr/bin/env python3
"""Integration tests for the Lan-Share relay.

Run from the project root:

    python3 -m unittest discover -s tests -v

Uses only the standard library plus Pillow (a runtime dependency) to build
fake JPEG frames, and the real ``server.main.ShareServer`` on an ephemeral
port - no screen capture or GUI is needed.
"""

import io
import socket
import threading
import time
import unittest

from PIL import Image

from core import health, protocol
from server.main import ShareServer


def fake_jpeg(color=(80, 120, 200), size=(160, 90)):
    """Return a tiny valid JPEG to use as a fake frame payload."""
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def recv(sock, timeout=3.0):
    sock.settimeout(timeout)
    return protocol.recv_message(sock)


def recv_type(sock, expected, timeout=3.0):
    """Receive messages until ``expected`` type; fail on ERROR/timeout."""
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    while True:
        header, payload = protocol.recv_message(sock)
        if header.get("type") == expected:
            return header, payload
        if header.get("type") == protocol.ERROR:
            raise AssertionError("unexpected ERROR: %s" % header)


class RelayTestBase(unittest.TestCase):
    def setUp(self):
        self.server = ShareServer(host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.start, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 5
        while True:
            sock = self.server._server_sock
            if sock is not None:
                try:
                    with socket.create_connection(
                        ("127.0.0.1", sock.getsockname()[1]), timeout=0.5
                    ):
                        break
                except OSError:
                    pass
            if time.monotonic() > deadline:
                self.fail("relay did not start")
            time.sleep(0.05)
        self.port = self.server._server_sock.getsockname()[1]

    def tearDown(self):
        self.server.stop()

    def connect(self):
        return socket.create_connection(("127.0.0.1", self.port), timeout=3)

    def register_sharer(self, stream="desk"):
        sock = self.connect()
        protocol.send_message(
            sock, {"type": protocol.SHARER_HELLO, "stream": stream, "host": "tester"}
        )
        header, _ = recv_type(sock, protocol.SHARER_REGISTERED)
        self.assertEqual(header.get("stream"), stream)
        return sock

    def subscribe(self, stream="desk"):
        sock = self.connect()
        protocol.send_message(sock, {"type": protocol.SUBSCRIBE, "stream": stream})
        header, _ = recv_type(sock, protocol.SUBSCRIBED)
        self.assertEqual(header.get("stream"), stream)
        return sock

    def send_synced_frame(self, sock, stream, seq, color=(80, 120, 200)):
        """Send one frame followed by a PING and wait for the matching PONG.

        The PONG is dispatched on the same connection *after* the frame, so
        once it is received the relay has fully processed the frame and
        stored it as the latest frame. This removes the race between frames
        still being dispatched and a viewer subscribing.
        """
        protocol.send_message(
            sock, {"type": protocol.FRAME, "stream": stream, "seq": seq},
            fake_jpeg(color=color),
        )
        sync_id = "sync-%s" % seq
        protocol.send_message(sock, {"type": protocol.PING, "id": sync_id})
        header, _ = recv_type(sock, protocol.PONG)
        self.assertEqual(header.get("id"), sync_id)


class TestRegistration(RelayTestBase):
    def test_sharer_receives_register_ack(self):
        sock = self.register_sharer("desk")
        self.server.stop()
        sock.close()

    def test_duplicate_sharer_replaces_old(self):
        first = self.register_sharer("desk")
        second = self.register_sharer("desk")
        self.assertEqual(
            self.server._sharers.get("desk").addr, second.getsockname()
        )
        # The old sharer's connection is closed by the relay.
        try:
            sock = first
            sock.settimeout(3)
            while True:
                protocol.recv_message(sock)
        except (OSError, protocol.ProtocolError):
            pass
        finally:
            first.close()
        second.close()


class TestSubscribe(RelayTestBase):
    def test_viewer_gets_latest_frame_after_subscribe(self):
        sharer = self.register_sharer("desk")
        self.send_synced_frame(sharer, "desk", 0, color=(255, 0, 0))
        self.send_synced_frame(sharer, "desk", 1, color=(0, 255, 0))
        viewer = self.subscribe("desk")
        header, payload = recv_type(viewer, protocol.FRAME)
        self.assertEqual(header.get("seq"), 1)
        self.assertEqual(payload, fake_jpeg(color=(0, 255, 0)))
        viewer.close()
        sharer.close()

    def test_unknown_stream_gets_error(self):
        viewer = self.connect()
        protocol.send_message(viewer, {"type": protocol.SUBSCRIBE, "stream": "nope"})
        header, _ = recv(viewer)
        self.assertEqual(header.get("type"), protocol.ERROR)
        self.assertEqual(header.get("reason"), protocol.UNKNOWN_STREAM)
        viewer.close()

    def test_stream_reuse_new_sharer_frames_flow(self):
        first = self.register_sharer("desk")
        self.send_synced_frame(first, "desk", 100, color=(255, 0, 0))
        second = self.register_sharer("desk")
        self.send_synced_frame(second, "desk", 200, color=(0, 0, 255))
        viewer = self.subscribe("desk")
        header, payload = recv_type(viewer, protocol.FRAME)
        self.assertEqual(header.get("seq"), 200)
        self.assertEqual(payload, fake_jpeg(color=(0, 0, 255)))
        viewer.close()
        second.close()
        try:
            first.close()
        except OSError:
            pass


class TestFrames(RelayTestBase):
    def test_slow_viewer_does_not_block_fast_viewer(self):
        sharer = self.register_sharer("desk")
        slow = self.subscribe("desk")
        # The slow viewer stops reading; its writer thread will block on send.
        fast = self.subscribe("desk")
        for seq in range(60):
            protocol.send_message(
                sharer, {"type": protocol.FRAME, "stream": "desk", "seq": seq},
                fake_jpeg(color=(10, 20, 30)),
            )
        start = time.monotonic()
        header, payload = recv_type(fast, protocol.FRAME, timeout=3.0)
        elapsed = time.monotonic() - start
        self.assertIsNotNone(payload)
        self.assertLess(elapsed, 3.0)
        fast.close()
        slow.close()
        sharer.close()

    def test_viewer_receives_stop_broadcast_when_sharer_leaves(self):
        sharer = self.register_sharer("desk")
        listener = self.connect()
        protocol.send_message(sharer, {"type": protocol.BYE})
        sharer.close()
        header, _ = recv_type(listener, protocol.STREAM_STOPPED, timeout=3.0)
        self.assertEqual(header.get("stream"), "desk")
        listener.close()


class TestPing(RelayTestBase):
    def test_ping_pong(self):
        sock = self.connect()
        protocol.send_message(sock, {"type": protocol.PING, "id": "abc"})
        header, _ = recv(sock)
        self.assertEqual(header.get("type"), protocol.PONG)
        self.assertEqual(header.get("id"), "abc")
        sock.close()


class TestHealth(RelayTestBase):
    def test_unreachable(self):
        result = health.check("127.0.0.1", 9, stream="desk", connect_timeout=1.0)
        self.assertEqual(result["status"], "unreachable")

    def test_stream_missing(self):
        result = health.check(
            "127.0.0.1", self.port, stream="ghost", connect_timeout=1.0
        )
        self.assertEqual(result["status"], "stream-missing")

    def test_no_frames(self):
        sharer = self.register_sharer("desk")
        result = health.check(
            "127.0.0.1", self.port, stream="desk",
            connect_timeout=1.0, frame_window=1.0,
        )
        self.assertEqual(result["status"], "no-frames")
        sharer.close()

    def test_active(self):
        sharer = self.register_sharer("desk")
        stop = threading.Event()

        def pump():
            seq = 0
            while not stop.is_set():
                protocol.send_message(
                    sharer, {"type": protocol.FRAME, "stream": "desk", "seq": seq},
                    fake_jpeg(),
                )
                seq += 1
                stop.wait(0.05)

        thread = threading.Thread(target=pump, daemon=True)
        thread.start()
        try:
            result = health.check(
                "127.0.0.1", self.port, stream="desk",
                connect_timeout=1.0, frame_window=3.0,
            )
        finally:
            stop.set()
            thread.join(timeout=1)
        self.assertEqual(result["status"], "active")
        sharer.close()


if __name__ == "__main__":
    unittest.main()
