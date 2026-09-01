#!/usr/bin/env python3
"""Lan-Share desktop app.

A native GTK3 window that mirrors the look of the old Flutter ``bundle`` app
(``com.screenshare.screen_share``) but works with the current Lan-Share relay
server setup:

- shows the sessions (streams) currently shared on the relay
- lets you share this screen under a session name - the name you type, or the
  PC host name by default
- lets you watch any session in a separate viewer window

Run it from the project root:

    python3 -m client.desktop --server 127.0.0.1 --port 8501
"""

import argparse
import json
import logging
import math
import os
import socket
import subprocess
import sys
import threading
import time

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GLib, Gdk, GdkPixbuf, Gtk
except (ImportError, ValueError) as exc:
    print("Lan-Share desktop app needs GTK3 (PyGObject). Install it with:")
    print("    sudo apt-get install python3-gi python3-gi-cairo gir1.2-gtk-3.0")
    raise SystemExit(1)

from core import discovery, protocol
from core.capture import ScreenCapture

logger = logging.getLogger("lan-share.desktop")

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
.hero-title { font-size: 20px; font-weight: 600; color: #94a3b8; }
.hero-sub { font-size: 13px; color: #8592a6; }
.card { background-color: #26263a; border: 1px solid #3a3a54; border-radius: 12px; }
.card-title {
    font-size: 11px; font-weight: 600; letter-spacing: 1px; color: #8592a6;
}
.field { font-size: 11px; color: #8592a6; }
entry {
    background-color: #1e1e2e; background-image: none;
    border: 1px solid #3a3a54; border-radius: 10px;
    color: #b9c4d4; padding: 6px 10px;
}
entry:focus { border-color: #66718f; }
button {
    background-color: #33344a; background-image: none;
    border: 1px solid #3a3a54; border-radius: 999px;
    color: #94a3b8; padding: 6px 16px;
}
button:hover { background-color: #505a76; color: #eef1f7; }
button.primary { background-color: #505a76; border-color: #66718f; color: #eef1f7; font-weight: 500; }
button.danger { background-color: #6b3a44; border-color: #8a4a56; color: #f3d8da; }
button:disabled { opacity: 0.45; }
.list-row { background-color: #1e1e2e; border-bottom: 1px solid #3a3a54; }
.list-row:hover { background-color: #26263a; }
#share-status { font-size: 12px; color: #8592a6; }
#status-bar { background-color: #1e1e2e; color: #8592a6; font-size: 11px; padding: 6px 14px; }
#stream-empty { font-size: 13px; color: #8592a6; }
#watch-title { font-size: 14px; color: #b9c4d4; }
"""

REFRESH_SECONDS = 3
SHARE_TARGET = (1600, 900)
NO_FRAME_TIMEOUT = 15.0

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "lan-share")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def _load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)


def rounded_rect(cr, x, y, w, h, r):
    """Add a rounded rectangle path to a cairo context."""
    r = min(r, w / 2.0, h / 2.0)
    cr.new_sub_path()
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.arc(x + w - r, y + r, r, 1.5 * math.pi, 2 * math.pi)
    cr.arc(x + w - r, y + h - r, r, 0, 0.5 * math.pi)
    cr.arc(x + r, y + h - r, r, 0.5 * math.pi, math.pi)
    cr.close_path()


class MonitorIcon(Gtk.DrawingArea):
    """The desktop-monitor icon from the old app, drawn with cairo."""

    def __init__(self, size=72):
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


class LanShareApp:
    def __init__(self, server, port, discovery_via=None):
        self.server = server or ""
        self.port = port
        self.discovery_via = discovery_via
        self.sharing = False
        self._share_stop = threading.Event()
        self._share_thread = None
        self._refreshing = False
        self._watch_window = None
        self._watch_image = None
        self._watch_msg = None
        self._watch_live_dot = None
        self._watch_stop = threading.Event()
        self._watch_thread = None
        self._relay_proc = None

        self._apply_css()
        self._build_ui()
        self.window.show_all()
        GLib.timeout_add(REFRESH_SECONDS * 1000, self._auto_refresh)
        GLib.timeout_add(400, self._initial_refresh)
        self._init_relay_state()

    def _init_relay_state(self):
        if not self.server:
            self._set_pill(self.pill_relay, "dot-err", "Relay: manual server required")
            self._set_hero(
                "Manual server required",
                "No relay was found on the LAN. Enter the relay PC's IP address "
                "in the Server field, or start the local relay below.",
            )
            return
        if self.discovery_via == "lan":
            self._set_pill(self.pill_relay, "dot", "Relay: discovered %s:%s" % (self.server, self.port))
        elif self.discovery_via == "localhost":
            self._set_pill(self.pill_relay, "dot", "Relay: localhost %s:%s" % (self.server, self.port))
        else:
            self._set_pill(self.pill_relay, "dot", "Relay: %s:%s" % (self.server, self.port))

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

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _ui_update(callback, *args):
        GLib.idle_add(callback, *args)

    def _new_dot(self, cls="dot"):
        dot = Gtk.Box()
        dot.set_size_request(8, 8)
        dot.get_style_context().add_class("dot")
        if cls != "dot":
            dot.get_style_context().add_class(cls)
        return dot

    def _new_pill(self, label_text, state=None):
        box = Gtk.Box(spacing=7)
        box.get_style_context().add_class("pill")
        box.pack_start(self._new_dot(state or "dot"), False, False, 0)
        label = Gtk.Label(label=label_text)
        box.pack_start(label, False, False, 0)
        box._label = label
        return box

    def _set_pill(self, pill, state, text):
        pill._label.set_text(text)
        dot = pill.get_children()[0]
        ctx = dot.get_style_context()
        for cls in ("dot-ok", "dot-err", "dot"):
            if cls in ctx.list_classes():
                ctx.remove_class(cls)
        ctx.add_class(state or "dot")

    def _new_entry(self, text="", width=10):
        entry = Gtk.Entry()
        entry.set_text(text)
        entry.set_width_chars(width)
        return entry

    def _field_row(self, label_text, entry):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        label = Gtk.Label(label=label_text, xalign=0)
        label.get_style_context().add_class("field")
        vbox.pack_start(label, False, False, 0)
        vbox.pack_start(entry, False, False, 0)
        return vbox

    def _set_status(self, text, state=None):
        self.status_bar.set_text(text)
        self._set_pill(self.pill_relay, state, text if text.startswith("Relay") else "Relay")

    def _set_hero(self, title, sub):
        self.hero_title.set_text(title)
        self.hero_sub.set_text(sub)

    # -- UI construction --------------------------------------------------

    def _build_ui(self):
        self.window = Gtk.Window(title="Lan-Share")
        self.window.set_default_size(880, 640)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.connect("destroy", Gtk.main_quit)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window.add(root)

        root.pack_start(self._build_header(), False, False, 0)
        root.pack_start(self._build_content(), True, True, 0)
        self.status_bar = Gtk.Label(label="", xalign=0)
        self.status_bar.set_name("status-bar")
        root.pack_start(self.status_bar, False, False, 0)

    def _build_header(self):
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        header.get_style_context().add_class("header")
        header.set_margin_top(10)
        header.set_margin_bottom(10)
        header.set_margin_start(20)
        header.set_margin_end(20)

        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        brand.pack_start(MonitorIcon(26), False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title = Gtk.Label(label="Lan-Share", xalign=0)
        title.get_style_context().add_class("brand-title")
        sub = Gtk.Label(label="screen sharing on your LAN", xalign=0)
        sub.get_style_context().add_class("brand-sub")
        texts.pack_start(title, False, False, 0)
        texts.pack_start(sub, False, False, 0)
        brand.pack_start(texts, False, False, 0)
        header.pack_start(brand, False, False, 0)

        pills = Gtk.Box(spacing=8)
        self.pill_relay = self._new_pill("Relay: unknown")
        self.pill_share = self._new_pill("Not sharing")
        pills.pack_start(self.pill_relay, False, False, 0)
        pills.pack_start(self.pill_share, False, False, 0)
        header.pack_end(pills, False, False, 0)
        return header

    def _build_content(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.get_style_context().add_class("content")
        scrolled.add(content)

        content.pack_start(self._build_hero(), False, False, 0)
        content.pack_start(self._build_share_card(), False, False, 0)
        content.pack_start(self._build_sessions_card(), False, False, 0)
        return scrolled

    def _build_hero(self):
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hero.set_valign(Gtk.Align.CENTER)
        hero.set_margin_top(28)
        hero.set_margin_bottom(24)
        hero.set_margin_start(20)
        hero.set_margin_end(20)
        icon = MonitorIcon(72)
        icon.set_halign(Gtk.Align.CENTER)
        hero.pack_start(icon, False, False, 0)
        self.hero_title = Gtk.Label(label="Connect to a host first")
        self.hero_title.get_style_context().add_class("hero-title")
        self.hero_title.set_halign(Gtk.Align.CENTER)
        self.hero_title.set_justify(Gtk.Justification.CENTER)
        self.hero_sub = Gtk.Label(label="Start the relay server, then share this screen or pick a session.")
        self.hero_sub.get_style_context().add_class("hero-sub")
        self.hero_sub.set_halign(Gtk.Align.CENTER)
        self.hero_sub.set_justify(Gtk.Justification.CENTER)
        hero.pack_start(self.hero_title, False, False, 0)
        hero.pack_start(self.hero_sub, False, False, 0)
        return hero

    def _build_share_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.get_style_context().add_class("card")
        card.set_margin_start(20)
        card.set_margin_end(20)
        card.set_margin_top(4)
        card.set_margin_bottom(16)

        title = Gtk.Label(label="SHARE THIS SESSION", xalign=0)
        title.get_style_context().add_class("card-title")
        card.pack_start(title, False, False, 0)

        self.entry_name = self._new_entry(socket.gethostname(), width=20)
        self.entry_server = self._new_entry(self.server, width=16)
        self.entry_port = self._new_entry(str(self.port), width=6)
        self.entry_fps = self._new_entry("10", width=5)
        self.entry_quality = self._new_entry("80", width=5)

        row1 = Gtk.Box(spacing=12)
        row1.pack_start(self._field_row("Session name (default: PC name)", self.entry_name), True, True, 0)
        row1.pack_start(self._field_row("Server IP", self.entry_server), False, False, 0)
        row1.pack_start(self._field_row("Port", self.entry_port), False, False, 0)
        card.pack_start(row1, False, False, 0)

        row2 = Gtk.Box(spacing=12)
        row2.pack_start(self._field_row("FPS", self.entry_fps), False, False, 0)
        row2.pack_start(self._field_row("Quality (1-100)", self.entry_quality), False, False, 0)
        row2.pack_start(self._field_row("Resolution", self._new_entry("1600x900", width=10)), False, False, 0)
        card.pack_start(row2, False, False, 0)

        buttons = Gtk.Box(spacing=10)
        self.btn_share = Gtk.Button(label="Start sharing")
        self.btn_share.get_style_context().add_class("primary")
        self.btn_share.connect("clicked", self._on_share_clicked)
        self.btn_stop = Gtk.Button(label="Stop")
        self.btn_stop.get_style_context().add_class("danger")
        self.btn_stop.set_sensitive(False)
        self.btn_stop.connect("clicked", self._on_stop_clicked)
        self.btn_relay = Gtk.Button(label="Start local relay")
        self.btn_relay.connect("clicked", self._on_start_relay_clicked)
        buttons.pack_start(self.btn_share, False, False, 0)
        buttons.pack_start(self.btn_stop, False, False, 0)
        buttons.pack_start(self.btn_relay, False, False, 0)
        card.pack_start(buttons, False, False, 0)

        self.share_status = Gtk.Label(label="Not sharing.", xalign=0)
        self.share_status.set_name("share-status")
        card.pack_start(self.share_status, False, False, 0)
        return card

    def _build_sessions_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("card")
        card.set_margin_start(20)
        card.set_margin_end(20)
        card.set_margin_bottom(20)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title = Gtk.Label(label="SESSIONS ON THE RELAY", xalign=0)
        title.get_style_context().add_class("card-title")
        head.pack_start(title, False, False, 0)
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", self._on_refresh_clicked)
        head.pack_end(refresh, False, False, 0)
        card.pack_start(head, False, False, 0)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        card.pack_start(self.listbox, False, False, 0)
        return card

    # -- sessions refresh -------------------------------------------------

    def list_streams(self):
        if not self.server:
            raise RuntimeError("no relay server configured - enter the server IP manually")
        sock = socket.create_connection((self.server, self.port), timeout=3)
        try:
            sock.settimeout(5)
            protocol.send_message(sock, {"type": protocol.STREAM_LIST})
            header, _ = protocol.recv_message(sock)
            if header.get("type") != protocol.STREAM_LIST_RESP:
                raise RuntimeError("unexpected reply: %s" % header)
            return header.get("streams", [])
        finally:
            sock.close()

    def _refresh_sessions(self):
        if self._refreshing:
            return
        self._refreshing = True
        if not self.server:
            self._ui_update(self._render_manual_required)
            return

        def work():
            try:
                streams = self.list_streams()
                self._ui_update(self._render_sessions, streams, None)
            except Exception as exc:
                self._ui_update(self._render_sessions, None, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _render_manual_required(self):
        self._refreshing = False
        for row in self.listbox.get_children():
            self.listbox.remove(row)
        label = Gtk.Label(
            label="No relay is configured. Enter the relay PC's IP address in the "
                  "Server field, then press Refresh.",
            xalign=0,
        )
        label.set_name("stream-empty")
        label.set_margin_top(12)
        label.set_margin_bottom(12)
        self.listbox.add(label)
        self.listbox.show_all()
        self._set_pill(self.pill_relay, "dot-err", "Relay: manual server required")
        self._set_hero(
            "Manual server required",
            "No relay was found on the LAN. Enter the relay PC's IP address in the "
            "Server field, or start the local relay below.",
        )

    def _render_sessions(self, streams, error):
        self._refreshing = False
        for row in self.listbox.get_children():
            self.listbox.remove(row)
        if error:
            label = Gtk.Label(
                label="Cannot reach the relay at %s:%s.\nStart it with: ./scripts/run_server.sh"
                % (self.server, self.port),
                xalign=0,
            )
            label.set_name("stream-empty")
            label.set_margin_top(12)
            label.set_margin_bottom(12)
            self.listbox.add(label)
            self._set_pill(self.pill_relay, "dot-err", "Relay: unreachable")
            self._set_hero("Cannot reach the relay", "The relay server is not responding at %s:%s. Start it with: ./scripts/run_server.sh" % (self.server, self.port))
            return
        if not streams:
            self._set_pill(self.pill_relay, "dot", "Relay: %s:%s" % (self.server, self.port))
            if not self.sharing:
                self._set_hero("Ready", "Share this screen with a session name, or watch a session below.")
            return
        for stream in streams:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.set_margin_start(10)
            row.set_margin_end(10)
            row.set_margin_top(7)
            row.set_margin_bottom(7)
            dot = self._new_dot("dot-ok")
            dot.set_margin_top(5)
            row.pack_start(dot, False, False, 0)
            name = Gtk.Label(label=stream, xalign=0, hexpand=True)
            watch = Gtk.Button(label="Watch")
            watch.connect("clicked", self._on_watch_clicked, stream)
            row.pack_start(name, True, True, 0)
            row.pack_start(watch, False, False, 0)
            self.listbox.add(row)
        self.listbox.show_all()
        self._set_pill(self.pill_relay, "dot-ok", "Relay: %s:%s" % (self.server, self.port))
        if not self.sharing:
            self._set_hero("Sessions found", "%d session(s) shared on the relay." % len(streams))

    def _auto_refresh(self):
        self._refresh_sessions()
        return True

    def _initial_refresh(self, *args):
        self._refresh_sessions()
        return False

    def _on_refresh_clicked(self, button):
        self._refresh_sessions()

    # -- sharing ----------------------------------------------------------

    def _on_share_clicked(self, button):
        name = self.entry_name.get_text().strip() or socket.gethostname()
        self.entry_name.set_text(name)
        try:
            server = self.entry_server.get_text().strip() or self.server
            port = int(self.entry_port.get_text().strip() or self.port)
            fps = max(1.0, float(self.entry_fps.get_text().strip() or 10))
            quality = max(1, min(100, int(self.entry_quality.get_text().strip() or 80)))
        except ValueError as exc:
            self.share_status.set_text("Invalid settings: %s" % exc)
            return

        self._share_stop.set()
        if self._share_thread is not None:
            self._share_thread.join(timeout=3)
        self._share_stop.clear()

        self.sharing = True
        self._share_thread = threading.Thread(
            target=self._share_worker,
            args=(server, port, name, fps, quality),
            daemon=True,
        )
        self._share_thread.start()
        self._set_sharing_ui(True)
        self.share_status.set_text('Connecting to relay %s:%s ...' % (server, port))
        self._set_pill(self.pill_share, "dot", "Connecting")
        self._set_hero('Sharing "%s"' % name, "Waiting for the relay to register the session ...")
        self._set_status("Connecting to %s:%s ..." % (server, port), None)

    def _share_worker(self, server, port, stream, fps, quality):
        sock = None
        try:
            sock = socket.create_connection((server, port), timeout=5)
            protocol.send_message(
                sock, {"type": protocol.SHARER_HELLO, "stream": stream, "host": stream}
            )
            try:
                self._wait_ack(sock, protocol.SHARER_REGISTERED, 5.0)
            except RuntimeError as exc:
                self._ui_update(self._on_share_failed, str(exc), server, port)
                return
            self._ui_update(self._on_share_registered, stream, server, port)
            sock.settimeout(None)
            capture = ScreenCapture(quality=quality, target_size=SHARE_TARGET)
            seq = 0
            while not self._share_stop.is_set():
                start = time.monotonic()
                payload = capture.grab_jpeg()
                protocol.send_message(
                    sock,
                    {"type": protocol.FRAME, "stream": stream, "seq": seq},
                    payload,
                )
                seq += 1
                elapsed = time.monotonic() - start
                self._share_stop.wait(max(0.0, 1.0 / fps - elapsed))
        except (OSError, protocol.ProtocolError) as exc:
            self._ui_update(self._on_share_failed, str(exc), server, port)
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
                    raise RuntimeError("relay refused: %s" % header.get("reason", "unknown"))
        except socket.timeout:
            raise RuntimeError("no %s reply from the relay within %.0fs" % (expected_type, timeout))

    def _on_share_registered(self, stream, server, port):
        self.share_status.set_text('Sharing "%s" on %s:%s' % (stream, server, port))
        self._set_pill(self.pill_share, "dot-ok", "Sharing")
        self._set_hero('Sharing "%s"' % stream, "Your screen is live on the relay. Watch it from another device.")
        self._set_status("Sharing %s on %s:%s" % (stream, server, port), "dot-ok")
        _save_config({"server": server, "port": port, "stream": stream})
        self._refresh_sessions()
        GLib.timeout_add(1500, self._refresh_sessions)

    def _on_share_failed(self, error, server, port):
        self.sharing = False
        self._set_sharing_ui(False)
        self.share_status.set_text("Share failed: %s" % error)
        self._set_pill(self.pill_share, "dot", "Not sharing")
        self._set_pill(self.pill_relay, "dot-err", "Relay: unreachable")
        self._set_hero("Cannot reach the relay", "The relay server is not responding at %s:%s. Start it with: ./scripts/run_server.sh" % (server, port))
        self._set_status("Relay unreachable at %s:%s" % (server, port), "dot-err")

    def _on_stop_clicked(self, button):
        self._stop_sharing()
        self.share_status.set_text("Sharing stopped.")
        self._set_pill(self.pill_share, "dot", "Not sharing")
        self._set_hero("Not sharing", "Share this screen with a session name, or watch a session below.")
        self._refresh_sessions()

    def _stop_sharing(self):
        self.sharing = False
        self._share_stop.set()
        if self._share_thread is not None:
            self._share_thread.join(timeout=3)
            self._share_thread = None
        self._set_sharing_ui(False)

    def _set_sharing_ui(self, sharing):
        self.btn_share.set_sensitive(not sharing)
        self.btn_stop.set_sensitive(sharing)
        self.entry_name.set_sensitive(not sharing)

    # -- watch ------------------------------------------------------------

    def _on_watch_clicked(self, button, stream):
        if self._watch_window is not None:
            self._watch_window.present()
            return

        self._watch_stop.clear()
        self._watch_last_pixbuf = None
        self._watch_window = Gtk.Window(title="Lan-Share - %s" % stream)
        self._watch_window.set_default_size(1600, 900)
        self._watch_window.set_position(Gtk.WindowPosition.CENTER)
        self._watch_window.connect("delete-event", self._on_watch_closed)
        self._watch_window.connect("key-press-event", self._on_watch_key)
        self._watch_window.connect("window-state-event", self._on_watch_state)

        overlay = Gtk.Overlay()
        self._watch_window.add(overlay)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vbox.get_style_context().add_class("content")
        overlay.add(vbox)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_top(6)
        header.set_margin_bottom(6)
        header.set_margin_start(12)
        header.set_margin_end(12)
        self._watch_live_dot = Gtk.Box()
        self._watch_live_dot.set_size_request(10, 10)
        self._watch_live_dot.get_style_context().add_class("dot")
        self._watch_live_dot.get_style_context().add_class("dot-ok")
        header.pack_start(self._watch_live_dot, False, False, 0)
        title = Gtk.Label(label="Lan-Share - %s" % stream, xalign=0, hexpand=True)
        title.set_name("watch-title")
        header.pack_start(title, True, True, 0)
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", self._on_watch_closed)
        header.pack_end(close_btn, False, False, 0)
        max_btn = Gtk.Button(label="Fullscreen")
        max_btn.connect("clicked", self._on_watch_maximize)
        header.pack_end(max_btn, False, False, 0)
        vbox.pack_start(header, False, False, 0)

        self._watch_image = Gtk.Image()
        self._watch_image.set_halign(Gtk.Align.FILL)
        self._watch_image.set_valign(Gtk.Align.FILL)
        self._watch_image.connect("size-allocate", self._on_watch_resize)
        vbox.pack_start(self._watch_image, True, True, 0)

        self._watch_msg = Gtk.Label(label="")
        self._watch_msg.set_name("stream-empty")
        self._watch_msg.set_halign(Gtk.Align.CENTER)
        self._watch_msg.set_valign(Gtk.Align.END)
        self._watch_msg.set_margin_top(16)
        self._watch_msg.set_margin_bottom(16)
        self._watch_msg.set_visible(False)
        overlay.add_overlay(self._watch_msg)

        self._watch_window.show_all()
        self._watch_thread = threading.Thread(
            target=self._watch_worker, args=(stream,), daemon=True
        )
        self._watch_thread.start()

    def _on_watch_resize(self, widget, alloc):
        if self._watch_last_pixbuf is not None:
            self._set_watch_pixbuf(self._watch_last_pixbuf)

    def _on_watch_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            if self._watch_window is not None:
                state = self._watch_window.get_window().get_state()
                if state & Gdk.WindowState.FULLSCREEN:
                    self._watch_window.unfullscreen()
                    return True
            self._on_watch_closed()
            return True
        return False

    def _on_watch_maximize(self, *args):
        if self._watch_window is None:
            return
        state = self._watch_window.get_window().get_state()
        if state & Gdk.WindowState.FULLSCREEN:
            self._watch_window.unfullscreen()
        else:
            self._watch_window.fullscreen()

    def _on_watch_state(self, widget, event):
        return False

    def _set_watch_pixbuf(self, pixbuf):
        """Scale the frame to fill the whole screen edge-to-edge.

        The image is scaled to cover the available area (keeping the aspect
        ratio, cropping any overflow), so the view is always full 16:9 with
        no bars. Frames are already captured at 1600x900, so on a 1600x900
        display this is an exact fill with no distortion.
        """
        self._watch_last_pixbuf = pixbuf
        if self._watch_image is None:
            return
        alloc = self._watch_image.get_allocation()
        width, height = alloc.width, alloc.height
        if width <= 1 or height <= 1:
            width, height = SHARE_TARGET
        sw, sh = pixbuf.get_width(), pixbuf.get_height()
        ratio = max(width / sw, height / sh)
        tw = max(width, int(sw * ratio))
        th = max(height, int(sh * ratio))
        try:
            scaled = pixbuf.scale_simple(tw, th, GdkPixbuf.InterpType.BILINEAR)
            if (tw, th) != (width, height):
                scaled = scaled.new_subpixbuf(
                    (tw - width) // 2, (th - height) // 2, width, height
                )
        except Exception:
            return
        self._watch_image.set_from_pixbuf(scaled)

    def _watch_worker(self, stream):
        sock = None
        try:
            sock = socket.create_connection((self.server, self.port), timeout=5)
            protocol.send_message(sock, {"type": protocol.SUBSCRIBE, "stream": stream})
            try:
                self._wait_ack(sock, protocol.SUBSCRIBED, 5.0)
            except RuntimeError as exc:
                self._ui_update(self._watch_fail, "Cannot watch '%s': %s" % (stream, exc))
                return
            sock.settimeout(NO_FRAME_TIMEOUT)
            while not self._watch_stop.is_set():
                try:
                    header, payload = protocol.recv_message(sock)
                except socket.timeout:
                    self._ui_update(
                        self._watch_fail,
                        "No frames received from '%s' for %.0fs - the sharer may have stopped."
                        % (stream, NO_FRAME_TIMEOUT),
                    )
                    self._watch_stop.set()
                    return
                except (OSError, protocol.ProtocolError):
                    break
                if header.get("type") == protocol.ERROR:
                    self._ui_update(self._watch_fail, "Stream not available: %s"
                                    % header.get("reason", "unknown"))
                    break
                if header.get("type") == protocol.FRAME and payload:
                    pixbuf = self._jpeg_to_pixbuf(payload)
                    if pixbuf is not None:
                        GLib.idle_add(self._set_watch_pixbuf, pixbuf)
        except (OSError, protocol.ProtocolError):
            self._ui_update(self._watch_fail, "Lost connection to the relay at %s:%s"
                            % (self.server, self.port))
        finally:
            if sock is not None:
                sock.close()

    def _watch_fail(self, message):
        self._watch_stop.set()
        if self._watch_msg is not None:
            self._watch_msg.set_text(message)
            self._watch_msg.set_visible(True)
        if self._watch_live_dot is not None:
            ctx = self._watch_live_dot.get_style_context()
            for cls in ("dot-ok", "dot-err"):
                ctx.remove_class(cls)
            ctx.add_class("dot-err")

    @staticmethod
    def _jpeg_to_pixbuf(jpeg_bytes):
        try:
            loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
            loader.write(jpeg_bytes)
            loader.close()
            return loader.get_pixbuf()
        except Exception:
            return None

    def _on_watch_closed(self, *args):
        self._watch_stop.set()
        if self._watch_thread is not None:
            self._watch_thread.join(timeout=2)
            self._watch_thread = None
        if self._watch_window is not None:
            self._watch_window.destroy()
            self._watch_window = None
            self._watch_image = None
            self._watch_msg = None
            self._watch_live_dot = None
            self._watch_last_pixbuf = None
        return True

    # -- local relay convenience ------------------------------------------

    def _on_start_relay_clicked(self, button):
        if self._relay_proc is not None and self._relay_proc.poll() is None:
            self._set_status("Relay server already running.", "dot-ok")
            return
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmd = [
            sys.executable, "-m", "server.main",
            "--host", "0.0.0.0", "--port", str(self.port),
        ]
        with open("/tmp/lan_share_relay.log", "a") as logf:
            self._relay_proc = subprocess.Popen(
                cmd, cwd=root, stdout=logf, stderr=logf, start_new_session=True
            )
        self._set_status("Local relay starting on port %d..." % self.port, None)
        GLib.timeout_add(1500, self._refresh_sessions)

    def run(self):
        self.window.show_all()
        Gtk.main()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lan-Share desktop app")
    parser.add_argument("--server", default=None, help="relay server IP on the LAN (default: auto-discover)")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    cfg = _load_config()
    server = args.server or cfg.get("server") or ""
    port = args.port or cfg.get("port") or 8501
    discovery_via = None

    if not server or server == "127.0.0.1":
        info = discovery.discover_detail(timeout=2.0)
        if info:
            server = info["ip"]
            discovery_via = info["via"]
            logger.info("relay discovered at %s:%s (%s)", info["ip"], info["port"], info["via"])
        else:
            logger.info("no relay discovered on the LAN - manual server required")
    logger.info("starting desktop app: relay %s:%s", server, port)

    GLib.set_prgname("Lan-Share")
    LanShareApp(server=server, port=port, discovery_via=discovery_via).run()


if __name__ == "__main__":
    main()
