#!/usr/bin/env python3
"""Lan-Share Relay control window.

A small native GTK3 window to manage the LAN relay server on this PC. It has
two buttons:

- "Start server" - starts the relay on 0.0.0.0:<port>
- "Stop server"  - stops the relay

Each action shows an info message, and a status pill always reflects whether
the server is actually listening on the port (it also detects a server that
was started from a terminal or another window).

Run it from the project root:

    python3 -m client.relay_control --port 8501
"""

import argparse
import logging
import math
import os
import re
import signal
import socket
import subprocess
import sys
import time

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import GLib, Gdk, Gtk
except (ImportError, ValueError) as exc:
    print("Lan-Share Relay control needs GTK3 (PyGObject). Install it with:")
    print("    sudo apt-get install python3-gi python3-gi-cairo gir1.2-gtk-3.0")
    raise SystemExit(1)

logger = logging.getLogger("lan-share.relay-control")

CSS = b"""
window { background-color: #2d2d3f; color: #94a3b8; }
.header { background-color: #1e1e2e; border-bottom: 1px solid #3a3a54; }
.brand-title { font-size: 15px; font-weight: 600; color: #b9c4d4; }
.brand-sub { font-size: 11px; color: #8592a6; }
.pill {
    background-color: #33344a; border: 1px solid #3a3a54;
    border-radius: 999px; padding: 4px 12px; font-size: 12px; color: #94a3b8;
}
.pill .dot { min-width: 8px; min-height: 8px; border-radius: 4px; background-color: #8592a6; }
.dot-ok { background-color: #46d17b; }
.dot-err { background-color: #e5736e; }
.content { background-color: #2d2d3f; }
.hero-title { font-size: 18px; font-weight: 600; color: #94a3b8; }
.hero-sub { font-size: 13px; color: #8592a6; }
.card { background-color: #26263a; border: 1px solid #3a3a54; border-radius: 12px; }
.field { font-size: 11px; color: #8592a6; }
button {
    background-color: #33344a; background-image: none;
    border: 1px solid #3a3a54; border-radius: 999px;
    color: #94a3b8; padding: 10px 22px; font-size: 13px;
}
button:hover { background-color: #505a76; color: #eef1f7; }
button.primary { background-color: #3d6b52; border-color: #46d17b; color: #eef1f7; font-weight: 500; }
button.danger { background-color: #6b3a44; border-color: #8a4a56; color: #f3d8da; }
#status-bar { background-color: #1e1e2e; color: #8592a6; font-size: 11px; padding: 6px 14px; }
#info {
    background-color: #1e1e2e; border: 1px solid #3a3a54; border-radius: 10px;
    color: #b9c4d4; font-size: 12px; padding: 10px 14px;
}
#info.ok { border-color: #46d17b; color: #a5e8bd; }
#info.err { border-color: #e5736e; color: #f3c2c0; }
#addr { font-size: 12px; color: #b9c4d4; }
"""

POLL_MS = 2000


def rounded_rect(cr, x, y, w, h, r):
    r = min(r, w / 2.0, h / 2.0)
    cr.new_sub_path()
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.arc(x + w - r, y + r, r, 1.5 * math.pi, 2 * math.pi)
    cr.arc(x + w - r, y + h - r, r, 0, 0.5 * math.pi)
    cr.arc(x + r, y + h - r, r, 0.5 * math.pi, math.pi)
    cr.close_path()


class MonitorIcon(Gtk.DrawingArea):
    def __init__(self, size=30):
        super().__init__()
        self._size = size
        self.set_size_request(size, int(size * 0.92))
        self.connect("draw", self._draw)

    def _draw(self, widget, cr):
        s = self.get_allocated_width() / 72.0
        cr.set_source_rgb(0x94 / 255.0, 0xA3 / 255.0, 0xB8 / 255.0)
        cr.set_line_width(3 * s)
        rounded_rect(cr, 4 * s, 2 * s, 64 * s, 40 * s, 5 * s)
        cr.stroke()
        cr.rectangle(4 * s, 36 * s, 64 * s, 6 * s)
        cr.fill()
        cr.rectangle(33 * s, 42 * s, 6 * s, 10 * s)
        cr.fill()
        cr.rectangle(24 * s, 57 * s, 24 * s, 4 * s)
        cr.fill()


class RelayControlApp:
    def __init__(self, port, host="0.0.0.0"):
        self.port = port
        self.host = host
        self._proc = None
        self._running = False

        self._apply_css()
        self._build_ui()
        self.window.show_all()
        GLib.timeout_add(POLL_MS, self._poll_status)
        self._refresh_status()

    # -- styling ----------------------------------------------------------

    @staticmethod
    def _apply_css():
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    # -- status -----------------------------------------------------------

    def _is_listening(self):
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                return True
        except OSError:
            return False

    def _refresh_status(self):
        self._running = self._is_listening()
        self._set_pill(
            "dot-ok" if self._running else "dot-err",
            "Running on %s:%d" % (self.host, self.port) if self._running else "Not running",
        )
        self.btn_start.set_sensitive(not self._running)
        self.btn_stop.set_sensitive(self._running)

    def _poll_status(self):
        self._refresh_status()
        if self._proc is not None and self._proc.poll() is not None:
            self._proc = None
        return True

    def _set_pill(self, state, text):
        self.pill_label.set_text(text)
        dot = self.pill.get_children()[0]
        ctx = dot.get_style_context()
        for cls in ("dot-ok", "dot-err", "dot"):
            if cls in ctx.list_classes():
                ctx.remove_class(cls)
        ctx.add_class(state or "dot")

    def _set_info(self, text, kind=None):
        self.info.set_text(text)
        ctx = self.info.get_style_context()
        for cls in ("ok", "err"):
            if cls in ctx.list_classes():
                ctx.remove_class(cls)
        if kind:
            ctx.add_class(kind)
        self.status_bar.set_text(text)

    # -- server control ---------------------------------------------------

    def _project_root(self):
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _on_start(self, button):
        if self._is_listening():
            self._set_info(
                "Server is already running on %s:%d." % (self.host, self.port),
                "ok",
            )
            return
        cmd = [
            sys.executable, "-m", "server.main",
            "--host", self.host, "--port", str(self.port),
        ]
        log_path = "/tmp/lan_share_relay.log"
        try:
            with open(log_path, "a") as logf:
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=self._project_root(),
                    stdout=logf,
                    stderr=logf,
                    start_new_session=True,
                )
        except OSError as exc:
            self._set_info("Failed to start server: %s" % exc, "err")
            return
        self._set_info("Server is starting on %s:%d ..." % (self.host, self.port))
        GLib.timeout_add(1200, self._confirm_started)

    def _confirm_started(self):
        if self._is_listening():
            self._refresh_status()
            self._set_info("Server started on %s:%d." % (self.host, self.port), "ok")
            self._set_info_append(" Log: /tmp/lan_share_relay.log")
        elif self._proc is not None and self._proc.poll() is not None:
            self._set_info(
                "Server failed to start - check /tmp/lan_share_relay.log.", "err"
            )
        return False

    def _set_info_append(self, suffix):
        self.info.set_text(self.info.get_text() + suffix)

    def _on_stop(self, button):
        if not self._is_listening():
            self._set_info("Server is not running.", "err")
            return
        for pid in self._find_server_pids(self.port):
            self._terminate(pid)
        if self._proc is not None:
            try:
                self._proc.terminate()
            except OSError:
                pass
            self._proc = None
        GLib.timeout_add(800, self._confirm_stopped)

    def _confirm_stopped(self):
        self._refresh_status()
        if self._is_listening():
            self._set_info("Server is still running - could not stop it.", "err")
        else:
            self._set_info("Server stopped.", "ok")
        return False

    @staticmethod
    def _find_server_pids(port):
        pids = []
        try:
            out = subprocess.run(
                ["ss", "-tlnp"], capture_output=True, text=True, timeout=5
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return pids
        for line in out.splitlines():
            if ":%d " % port in line and "pid=" in line:
                m = re.search(r"pid=(\d+)", line)
                if m:
                    pids.append(int(m.group(1)))
        return pids

    @staticmethod
    def _terminate(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
        deadline = time.time() + 2
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.1)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    # -- UI construction --------------------------------------------------

    def _build_ui(self):
        self.window = Gtk.Window(title="Lan-Share Relay")
        self.window.set_default_size(520, 320)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.connect("destroy", Gtk.main_quit)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window.add(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        header.get_style_context().add_class("header")
        header.set_margin_top(10)
        header.set_margin_bottom(10)
        header.set_margin_start(20)
        header.set_margin_end(20)

        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        brand.pack_start(MonitorIcon(30), False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title = Gtk.Label(label="Lan-Share Relay", xalign=0)
        title.get_style_context().add_class("brand-title")
        sub = Gtk.Label(label="local relay server control", xalign=0)
        sub.get_style_context().add_class("brand-sub")
        texts.pack_start(title, False, False, 0)
        texts.pack_start(sub, False, False, 0)
        brand.pack_start(texts, False, False, 0)
        header.pack_start(brand, False, False, 0)

        self.pill = Gtk.Box(spacing=7)
        self.pill.get_style_context().add_class("pill")
        dot = Gtk.Box()
        dot.set_size_request(8, 8)
        dot.get_style_context().add_class("dot")
        self.pill.pack_start(dot, False, False, 0)
        self.pill_label = Gtk.Label(label="Not running")
        self.pill.pack_start(self.pill_label, False, False, 0)
        header.pack_end(self.pill, False, False, 0)

        root.pack_start(header, False, False, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.get_style_context().add_class("content")
        content.set_margin_top(28)
        content.set_margin_bottom(24)
        content.set_margin_start(32)
        content.set_margin_end(32)
        root.pack_start(content, True, True, 0)

        hero = Gtk.Label(label="Start or stop the relay server on this PC.", xalign=0)
        hero.get_style_context().add_class("hero-sub")
        content.pack_start(hero, False, False, 0)

        addr = Gtk.Label(label="Listening address:  %s:%d" % (self.host, self.port), xalign=0)
        addr.get_style_context().add_class("addr")
        addr.set_name("addr")
        content.pack_start(addr, False, False, 0)

        buttons = Gtk.Box(spacing=12)
        self.btn_start = Gtk.Button(label="Start server")
        self.btn_start.get_style_context().add_class("primary")
        self.btn_start.connect("clicked", self._on_start)
        self.btn_stop = Gtk.Button(label="Stop server")
        self.btn_stop.get_style_context().add_class("danger")
        self.btn_stop.connect("clicked", self._on_stop)
        buttons.pack_start(self.btn_start, False, False, 0)
        buttons.pack_start(self.btn_stop, False, False, 0)
        content.pack_start(buttons, False, False, 0)

        self.info = Gtk.Label(label="", xalign=0, wrap=True)
        self.info.set_name("info")
        content.pack_start(self.info, False, False, 0)

        self.status_bar = Gtk.Label(label="", xalign=0)
        self.status_bar.set_name("status-bar")
        root.pack_start(self.status_bar, False, False, 0)

    def run(self):
        self.window.show_all()
        Gtk.main()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lan-Share Relay control window")
    parser.add_argument("--host", default="0.0.0.0", help="address the server binds to")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.info("starting relay control window on %s:%s", args.host, args.port)

    GLib.set_prgname("Lan-Share Relay")
    RelayControlApp(port=args.port, host=args.host).run()


if __name__ == "__main__":
    main()
