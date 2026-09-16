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
from client import theme

logger = logging.getLogger("lan-share.desktop")

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
        self.light = False
        self.sharing = False
        self._share_stop = threading.Event()
        self._share_thread = None
        self._share_sock = None
        self._share_name = ""
        self._share_region = None
        self._share_has_control = False
        self._refreshing = False
        self._watch_window = None
        self._watch_image = None
        self._watch_msg = None
        self._watch_live_dot = None
        self._watch_fps_label = None
        self._watch_age_label = None
        self._watch_stop = threading.Event()
        self._watch_thread = None
        self._watch_stream = None
        self._watch_sock = None
        self._watch_has_control = False
        self._watch_video_raw_size = None
        self._watch_header_revealer = None
        self._watch_toggle_btn = None
        self._watch_header_timer = None
        self._watch_eventbox = None
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

    def _apply_css(self):
        if getattr(self, "_css_provider", None) is not None:
            Gtk.StyleContext.remove_provider_for_screen(
                Gdk.Screen.get_default(), self._css_provider
            )
        provider = Gtk.CssProvider()
        palette = theme.LIGHT if self.light else theme.DARK
        provider.load_from_data(theme.gtk_css(palette))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._css_provider = provider

    def _toggle_theme(self, button=None):
        self.light = not self.light
        self._apply_css()
        if self._btn_theme is not None:
            self._btn_theme.set_label("Light" if self.light else "Dark")

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
        self.status_bar.set_text(title)
        if hasattr(self, "share_status"):
            self.share_status.set_text(sub)

    # -- UI construction --------------------------------------------------

    def _build_ui(self):
        self.window = Gtk.Window(title="Lan-Share")
        self.window.set_default_size(1120, 700)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.connect("destroy", Gtk.main_quit)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window.add(root)

        root.pack_start(self._build_header(), False, False, 0)
        root.pack_start(self._build_content(), True, True, 0)
        self.status_bar = Gtk.Label(
            label="Idle  •  Ready to share or connect", xalign=0
        )
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
        self._btn_theme = Gtk.Button(label="Dark")
        self._btn_theme.get_style_context().add_class("small")
        self._btn_theme.connect("clicked", self._toggle_theme)
        pills.pack_start(self.pill_relay, False, False, 0)
        pills.pack_start(self.pill_share, False, False, 0)
        pills.pack_start(self._btn_theme, False, False, 0)
        header.pack_end(pills, False, False, 0)
        return header

    def _build_content(self):
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        content.get_style_context().add_class("content")

        left = self._build_host_card()
        right = self._build_sessions_card()
        left.set_hexpand(True)
        right.set_hexpand(True)
        content.pack_start(left, True, True, 0)
        content.pack_start(right, True, True, 0)
        return content

    def _build_host_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("card")

        title = Gtk.Label(label="Broadcast / Host", xalign=0)
        title.get_style_context().add_class("card-title")
        card.pack_start(title, False, False, 0)

        self.entry_name = self._new_entry(socket.gethostname(), width=20)
        card.pack_start(self._field_row("Session name", self.entry_name), False, False, 0)

        self.combo_capture = self._build_capture_combo()
        card.pack_start(self._field_row("Capture source", self.combo_capture), False, False, 0)

        self._window_box = Gtk.Box(spacing=12)
        self._window_box.set_no_show_all(True)
        self._window_box.set_visible(False)
        self.combo_window = Gtk.ComboBoxText()
        self.combo_window.append("none", "(click Refresh)")
        btn_win = Gtk.Button(label="Refresh")
        btn_win.get_style_context().add_class("small")
        btn_win.connect("clicked", self._refresh_windows)
        self._window_box.pack_start(self._field_row("Window", self.combo_window), True, True, 0)
        self._window_box.pack_start(btn_win, False, False, 0)
        card.pack_start(self._window_box, False, False, 0)

        self._region_box = Gtk.Box(spacing=8)
        self._region_box.set_no_show_all(True)
        self._region_box.set_visible(False)
        sp_x = self._new_entry("0", width=4); sp_y = self._new_entry("0", width=4)
        sp_w = self._new_entry("1600", width=5); sp_h = self._new_entry("900", width=5)
        self._region_fields = (sp_x, sp_y, sp_w, sp_h)
        for lbl, sp in [("X", sp_x), ("Y", sp_y), ("W", sp_w), ("H", sp_h)]:
            self._region_box.pack_start(self._field_row(lbl, sp), True, True, 0)
        card.pack_start(self._region_box, False, False, 0)

        perf = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        perf_title = Gtk.Label(label="Performance", xalign=0)
        perf_title.get_style_context().add_class("field")
        perf.pack_start(perf_title, False, False, 0)

        fps_row = Gtk.Box(spacing=4)
        fps_row.get_style_context().add_class("segmented")
        self._fps_buttons = []
        self._fps_value = 30
        for i, val in enumerate((15, 30, 60)):
            btn = Gtk.ToggleButton(label=str(val))
            btn.get_style_context().add_class("seg")
            btn.set_active(i == 1)
            btn.connect("toggled", self._on_fps_toggled, val)
            fps_row.pack_start(btn, True, True, 0)
            self._fps_buttons.append(btn)
        perf.pack_start(fps_row, False, False, 0)

        self.scale_quality = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 1, 100, 1
        )
        self.scale_quality.set_value(80)
        self.scale_quality.set_hexpand(True)
        self.scale_quality.set_draw_value(False)
        self.scale_quality.connect("value-changed", self._on_quality_changed)
        self.lbl_quality = Gtk.Label(label="80%", xalign=0)
        self.lbl_quality.set_name("quality-label")
        qrow = Gtk.Box(spacing=10)
        qrow.pack_start(Gtk.Label(label="Quality", xalign=0), False, False, 0)
        qrow.pack_start(self.scale_quality, True, True, 0)
        qrow.pack_start(self.lbl_quality, False, False, 0)
        perf.pack_start(qrow, False, False, 0)
        card.pack_start(perf, False, False, 0)

        self.entry_server = self._new_entry(self.server, width=14)
        self.entry_port = self._new_entry(str(self.port), width=6)
        srv_row = Gtk.Box(spacing=12)
        srv_row.pack_start(self._field_row("Server IP", self.entry_server), True, True, 0)
        srv_row.pack_start(self._field_row("Port", self.entry_port), False, False, 0)
        card.pack_start(srv_row, False, False, 0)

        self.btn_cta = Gtk.Button(label="Start sharing")
        self.btn_cta.get_style_context().add_class("primary")
        self.btn_cta.set_hexpand(True)
        self.btn_cta.connect("clicked", self._on_cta_clicked)
        card.pack_start(self.btn_cta, False, False, 0)

        self.btn_relay = Gtk.Button(label="Start local relay")
        self.btn_relay.set_hexpand(True)
        self.btn_relay.connect("clicked", self._on_start_relay_clicked)
        card.pack_start(self.btn_relay, False, False, 0)

        self.share_status = Gtk.Label(label="Not sharing.", xalign=0)
        self.share_status.set_name("share-status")
        card.pack_start(self.share_status, False, False, 0)
        return card

    def _on_fps_toggled(self, button, value):
        if button.get_active():
            self._fps_value = value
            for other in self._fps_buttons:
                if other is not button:
                    other.set_active(False)

    def _on_quality_changed(self, scale):
        self.lbl_quality.set_text("%d%%" % round(scale.get_value()))

    def _build_capture_combo(self):
        self.combo_capture = Gtk.ComboBoxText()
        self.combo_capture.append("full", "Full screen (all monitors)")
        self.combo_capture.append("primary", "Primary monitor")
        self.combo_capture.append("window", "Window")
        self.combo_capture.append("region", "Region")
        self.combo_capture.set_active_id("full")
        self.combo_capture.connect("changed", self._on_capture_mode_changed)
        return self.combo_capture

    def _on_capture_mode_changed(self, combo):
        mode = combo.get_active_id()
        self._region_box.set_visible(mode == "region")
        self._window_box.set_visible(mode == "window")

    def _refresh_windows(self, button=None):
        self.combo_window.remove_all()
        for w in self._wmctrl_windows():
            self.combo_window.append(w["id"], "%s  (%d,%d %d×%d)" % (
                w["title"] or "Window " + w["id"], w["x"], w["y"], w["w"], w["h"],
            ))
        self.combo_window.set_active(0)
        self.combo_window.show_all()

    @staticmethod
    def _wmctrl_windows():
        wins = []
        try:
            out = subprocess.run(
                ["wmctrl", "-lG"], capture_output=True, text=True, timeout=3,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return wins
        for line in out.splitlines():
            parts = line.split(None, 12)
            if len(parts) < 12:
                continue
            wid, desktop, x, y, w, h = parts[0], parts[1], *parts[2:6]
            title = parts[11] if len(parts) > 11 else ""
            try:
                wins.append({"id": wid, "x": int(x), "y": int(y),
                             "w": int(w), "h": int(h), "title": title})
            except ValueError:
                continue
        return wins

    def _capture_region(self):
        mode = self.combo_capture.get_active_id()
        if mode == "primary":
            scr = Gdk.Screen.get_default()
            geo = scr.get_primary_monitor().get_geometry()
            return (geo.x, geo.y, geo.width, geo.height)
        if mode == "region":
            try:
                x = int(self._region_fields[0].get_text().strip())
                y = int(self._region_fields[1].get_text().strip())
                w = max(1, int(self._region_fields[2].get_text().strip()))
                h = max(1, int(self._region_fields[3].get_text().strip()))
            except ValueError:
                return None
            return (x, y, w, h)
        if mode == "window":
            wid = self.combo_window.get_active_id()
            if not wid or wid == "none":
                return None
            for w in self._wmctrl_windows():
                if w["id"] == wid:
                    return (w["x"], w["y"], w["w"], w["h"])
        return None

    def _capture_size(self, region):
        if not region:
            return SHARE_TARGET
        w, h = region[2], region[3]
        ratio = min(SHARE_TARGET[0] / max(w, 1), SHARE_TARGET[1] / max(h, 1))
        return (max(1, round(w * ratio)), max(1, round(h * ratio)))

    def _build_sessions_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("card")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Available Sessions", xalign=0, hexpand=True)
        title.get_style_context().add_class("card-title")
        head.pack_start(title, True, True, 0)
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", self._on_refresh_clicked)
        head.pack_end(refresh, False, False, 0)
        card.pack_start(head, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.get_style_context().add_class("session-list")
        scroll.add(self.listbox)
        card.pack_start(scroll, True, True, 0)
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
            streams = header.get("streams", [])
            meta = {
                "viewer_counts": header.get("viewer_counts", {}),
                "last_frame_age": header.get("last_frame_age", {}),
                "uptime": header.get("uptime", {}),
                "hosts": header.get("hosts", {}),
                "addresses": header.get("addresses", {}),
                "fps": header.get("fps", {}),
                "sizes": header.get("sizes", {}),
            }
            return streams, meta
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
                streams, meta = self.list_streams()
                self._ui_update(self._render_sessions, streams, meta, None)
            except Exception as exc:
                self._ui_update(self._render_sessions, None, {}, str(exc))

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

    def _render_sessions(self, streams, meta, error):
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
            self._set_hero(
                "Relay unreachable",
                "The relay server is not responding at %s:%s. Start it with: ./scripts/run_server.sh"
                % (self.server, self.port),
            )
            return
        if not streams:
            self._set_pill(self.pill_relay, "dot", "Relay: %s:%s" % (self.server, self.port))
            if self.sharing and getattr(self, "_share_name", ""):
                self._set_hero('Sharing "%s"' % self._share_name, "Your screen is live on the relay.")
            else:
                self._set_hero("Idle  •  Ready to share or connect", "No sessions on the relay yet.")
            return
        for stream in streams:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            row.get_style_context().add_class("session-card")
            top = Gtk.Box(spacing=8)
            dot = self._new_dot("dot-ok")
            dot.set_margin_top(6)
            name = Gtk.Label(label=stream, xalign=0, hexpand=True)
            name.get_style_context().add_class("session-name")
            view_btn = Gtk.Button(label="Watch")
            view_btn.get_style_context().add_class("watch-btn")
            view_btn.connect("clicked", self._on_watch_clicked, stream)
            top.pack_start(dot, False, False, 0)
            top.pack_start(name, True, True, 0)
            top.pack_end(view_btn, False, False, 0)
            viewers = meta.get("viewer_counts", {}).get(stream, 0)
            age = meta.get("last_frame_age", {}).get(stream)
            up  = meta.get("uptime", {}).get(stream)
            host = meta.get("hosts", {}).get(stream, stream)
            addr = meta.get("addresses", {}).get(stream, "")
            fps = meta.get("fps", {}).get(stream)
            size = meta.get("sizes", {}).get(stream)
            parts = []
            if addr:
                parts.append("IP %s" % addr)
            if size:
                parts.append("%d×%d" % (size[0], size[1]))
            if fps:
                parts.append("%d fps" % int(fps))
            parts.append("%d watching" % viewers)
            if age is not None:
                parts.append("frame %.1fs ago" % age)
            else:
                parts.append("no frames yet")
            if up is not None:
                parts.append("up %ds" % int(up))
            sub = Gtk.Label(
                label="·".join([" " + p + " " for p in parts]),
                xalign=0,
            )
            sub.get_style_context().add_class("meta")
            if age is not None and age > 10:
                sub.get_style_context().add_class("meta-err")
            elif age is not None:
                sub.get_style_context().add_class("meta-ok")
            if host and host != stream:
                host_line = Gtk.Label(label=host, xalign=0)
                host_line.get_style_context().add_class("session-host")
                row.pack_start(host_line, False, False, 0)
            row.pack_start(top, False, False, 0)
            row.pack_start(sub, False, False, 0)
            self.listbox.add(row)
        self.listbox.show_all()
        self._set_pill(self.pill_relay, "dot-ok", "Relay: %s:%s" % (self.server, self.port))
        if not self.sharing:
            self._set_hero("Idle  •  Ready to share or connect", "%d session(s) shared on the relay." % len(streams))

    def _auto_refresh(self):
        self._refresh_sessions()
        return True

    def _initial_refresh(self, *args):
        self._refresh_sessions()
        return False

    def _on_refresh_clicked(self, button):
        self._refresh_sessions()

    # -- sharing ----------------------------------------------------------

    def _on_cta_clicked(self, button):
        if self.sharing:
            self._on_stop_clicked(button)
        else:
            self._on_share_clicked(button)

    def _on_share_clicked(self, button):
        name = self.entry_name.get_text().strip() or socket.gethostname()
        self.entry_name.set_text(name)
        try:
            server = self.entry_server.get_text().strip() or self.server
            port = int(self.entry_port.get_text().strip() or self.port)
            fps = float(self._fps_value)
            quality = int(round(self.scale_quality.get_value()))
        except ValueError as exc:
            self.share_status.set_text("Invalid settings: %s" % exc)
            return

        region = self._capture_region()

        self._share_stop.set()
        if self._share_thread is not None:
            self._share_thread.join(timeout=3)
        self._share_stop.clear()

        self.sharing = True
        self._share_name = name
        self._share_thread = threading.Thread(
            target=self._share_worker,
            args=(server, port, name, fps, quality, region),
            daemon=True,
        )
        self._share_thread.start()
        self._set_sharing_ui(True)
        self.share_status.set_text('Connecting to relay %s:%s ...' % (server, port))
        self._set_pill(self.pill_share, "dot", "Connecting")
        self._set_hero('Sharing "%s"' % name, "Waiting for the relay to register the session ...")
        self._set_status("Connecting to %s:%s ..." % (server, port), None)

    def _share_worker(self, server, port, stream, fps, quality, region):
        sock = None
        try:
            sock = socket.create_connection((server, port), timeout=5)
            width, height = self._capture_size(region)
            protocol.send_message(
                sock,
                {
                    "type": protocol.SHARER_HELLO,
                    "stream": stream,
                    "host": stream,
                    "fps": fps,
                    "width": width,
                    "height": height,
                },
            )
            try:
                self._wait_ack(sock, protocol.SHARER_REGISTERED, 5.0)
            except RuntimeError as exc:
                self._ui_update(self._on_share_failed, str(exc), server, port)
                return
            self._share_sock = sock
            self._share_name = stream
            self._share_region = region
            self._ui_update(self._on_share_registered, stream, server, port)
            sock.settimeout(None)
            reader = threading.Thread(
                target=self._share_reader, args=(sock,), daemon=True
            )
            reader.start()
            capture = ScreenCapture(
                region=region,
                quality=quality,
                target_size=SHARE_TARGET,
                show_cursor=True,
            )
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
            self._share_sock = None
            if sock is not None:
                try:
                    protocol.send_message(sock, {"type": protocol.BYE})
                except OSError:
                    pass
                sock.close()

    def _share_reader(self, sock, timeout=1.0):
        """Read control messages from the relay on the share connection."""
        sock.settimeout(timeout)
        try:
            while not self._share_stop.is_set():
                try:
                    header, _ = protocol.recv_message(sock)
                except socket.timeout:
                    continue
                except (OSError, protocol.ProtocolError):
                    break
                self._on_control_message(header)
        except Exception:
            pass

    def _on_control_message(self, header):
        msg_type = header.get("type")
        if msg_type == protocol.CONTROL_REQUEST:
            GLib.idle_add(self._show_control_request, header)
        elif msg_type == protocol.CONTROL_INPUT:
            self._inject_input(header.get("evt") or {})
        elif msg_type == protocol.CONTROL_REVOKED:
            self._share_has_control = False

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
        self._set_hero("Relay unreachable", "The relay server is not responding at %s:%s. Start it with: ./scripts/run_server.sh" % (server, port))
        self._set_status("Relay unreachable at %s:%s" % (server, port), "dot-err")

    def _on_stop_clicked(self, button):
        self._stop_sharing()
        self.share_status.set_text("Sharing stopped.")
        self._set_pill(self.pill_share, "dot", "Not sharing")
        self._set_hero("Idle  •  Ready to share or connect", "Share this screen with a session name, or watch a session below.")
        self._refresh_sessions()

    def _stop_sharing(self):
        self.sharing = False
        self._share_stop.set()
        if self._share_thread is not None:
            self._share_thread.join(timeout=3)
            self._share_thread = None
        self._set_sharing_ui(False)

    def _set_sharing_ui(self, sharing):
        self.btn_cta.set_label("Stop sharing" if sharing else "Start sharing")
        ctx = self.btn_cta.get_style_context()
        if sharing:
            ctx.add_class("danger")
            ctx.remove_class("primary")
        else:
            ctx.add_class("primary")
            ctx.remove_class("danger")
        self.entry_name.set_sensitive(not sharing)

    # -- remote control (sharer side) ------------------------------------

    def _show_control_request(self, header):
        if not self.sharing:
            return False
        host = header.get("viewer_host", "A remote viewer")
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            title="Control request",
            text='%s wants to control your screen' % host,
        )
        dialog.format_secondary_text(
            "Allow the remote viewer to move your mouse and click on this screen? "
            "You can press Stop Sharing at any time to take back control."
        )
        granted = dialog.run() == Gtk.ResponseType.YES
        dialog.destroy()
        self._reply_control(granted, header.get("viewer_id"))
        return False

    def _reply_control(self, granted, viewer_id):
        if not self.sharing or self._share_sock is None:
            return
        try:
            protocol.send_message(
                self._share_sock,
                {
                    "type": protocol.CONTROL_GRANTED if granted else protocol.CONTROL_DENIED,
                    "stream": self._share_name,
                    "viewer_id": viewer_id,
                },
            )
        except OSError:
            pass

    def _inject_input(self, evt):
        """Play back a remote mouse event via XTest on the sharer's screen."""
        try:
            from Xlib import X, display
            from Xlib.ext import xtest
        except ImportError:
            return
        disp = None
        try:
            disp = display.Display()
            root = disp.screen().root
            region = self._share_region
            if region:
                ox, oy, ow, oh = region[0], region[1], region[2], region[3]
            else:
                scr = Gdk.Screen.get_default()
                ox, oy = 0, 0
                ow, oh = scr.get_width(), scr.get_height()
            evt_type = evt.get("type")
            if evt_type == "motion":
                x = ox + int(float(evt.get("x", 0)) * ow)
                y = oy + int(float(evt.get("y", 0)) * oh)
                root.warp_pointer(max(0, x), max(0, y))
            elif evt_type == "button":
                button = int(evt.get("button", 1))
                pressed = bool(evt.get("pressed", True))
                xtest.fake_input(
                    disp,
                    X.ButtonPress if pressed else X.ButtonRelease,
                    button,
                )
            elif evt_type == "scroll":
                button = int(evt.get("button", 4))
                xtest.fake_input(disp, X.ButtonPress, button)
                xtest.fake_input(disp, X.ButtonRelease, button)
            disp.sync()
        except Exception:
            pass
        finally:
            if disp is not None:
                try:
                    disp.close()
                except Exception:
                    pass

    # -- watch ------------------------------------------------------------

    def _on_watch_clicked(self, button, stream):
        if self._watch_window is not None:
            self._watch_window.present()
            return

        self._watch_stop.clear()
        self._watch_last_pixbuf = None
        self._watch_last_frame_time = None
        self._watch_frame_times = []
        self._watch_stream = stream
        self._watch_has_control = False

        self._watch_window = Gtk.Window(title="Lan-Share - %s" % stream)
        self._watch_window.set_default_size(1600, 900)
        self._watch_window.set_position(Gtk.WindowPosition.CENTER)
        self._watch_window.set_decorated(True)
        self._watch_window.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        self._watch_window.connect("delete-event", self._on_watch_closed)
        self._watch_window.connect("key-press-event", self._on_watch_key)
        self._watch_window.connect("window-state-event", self._on_watch_state)

        overlay = Gtk.Overlay()
        self._watch_window.add(overlay)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay.add(vbox)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.get_style_context().add_class("watch-bar")
        header.set_margin_top(4); header.set_margin_bottom(4)
        header.set_margin_start(12); header.set_margin_end(12)
        self._watch_live_dot = Gtk.Box()
        self._watch_live_dot.set_size_request(10, 10)
        self._watch_live_dot.get_style_context().add_class("dot")
        self._watch_live_dot.get_style_context().add_class("dot-ok")
        header.pack_start(self._watch_live_dot, False, False, 0)
        title = Gtk.Label(label="Lan-Share - %s" % stream, xalign=0, hexpand=True)
        title.set_name("watch-title")
        header.pack_start(title, True, True, 0)
        self._watch_fps_label = Gtk.Label(label="", xalign=0)
        self._watch_fps_label.get_style_context().add_class("meta")
        self._watch_age_label = Gtk.Label(label="", xalign=0)
        self._watch_age_label.get_style_context().add_class("meta")
        header.pack_end(self._watch_age_label, False, False, 0)
        header.pack_end(Gtk.Label(label="·"), False, False, 0)
        header.pack_end(self._watch_fps_label, False, False, 0)
        self._btn_control = Gtk.Button(label="Request Control")
        self._btn_control.get_style_context().add_class("primary")
        self._btn_control.connect("clicked", self._on_request_control)
        header.pack_end(self._btn_control, False, False, 0)

        self._watch_header_revealer = Gtk.Revealer()
        self._watch_header_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        self._watch_header_revealer.set_transition_duration(180)
        self._watch_header_revealer.set_reveal_child(True)
        self._watch_header_revealer.add(header)
        vbox.pack_start(self._watch_header_revealer, False, False, 0)

        self._watch_eventbox = Gtk.EventBox()
        self._watch_eventbox.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.SCROLL_MASK
        )
        self._watch_image = Gtk.Image()
        self._watch_image.set_halign(Gtk.Align.FILL)
        self._watch_image.set_valign(Gtk.Align.FILL)
        self._watch_image.connect("size-allocate", self._on_watch_resize)
        self._watch_eventbox.add(self._watch_image)
        self._watch_eventbox.connect("motion-notify-event", self._on_watch_motion)
        self._watch_eventbox.connect("motion-notify-event", self._on_watch_hover)
        self._watch_eventbox.connect("button-press-event", self._on_watch_button)
        self._watch_eventbox.connect("button-release-event", self._on_watch_button_release)
        self._watch_eventbox.connect("scroll-event", self._on_watch_scroll)
        vbox.pack_start(self._watch_eventbox, True, True, 0)

        self._watch_toggle_btn = Gtk.Button(label="\u25be")
        self._watch_toggle_btn.get_style_context().add_class("watch-toggle")
        self._watch_toggle_btn.set_valign(Gtk.Align.START)
        self._watch_toggle_btn.set_halign(Gtk.Align.START)
        self._watch_toggle_btn.set_margin_top(6)
        self._watch_toggle_btn.set_margin_start(6)
        self._watch_toggle_btn.connect("clicked", self._on_watch_toggle)
        overlay.add_overlay(self._watch_toggle_btn)

        self._watch_msg = Gtk.Label(label="")
        self._watch_msg.set_name("stream-empty")
        self._watch_msg.set_halign(Gtk.Align.CENTER)
        self._watch_msg.set_valign(Gtk.Align.END)
        self._watch_msg.set_margin_top(16); self._watch_msg.set_margin_bottom(16)
        self._watch_msg.set_visible(False)
        overlay.add_overlay(self._watch_msg)

        self._watch_window.show_all()
        self._watch_thread = threading.Thread(
            target=self._watch_worker, args=(stream,), daemon=True
        )
        self._watch_thread.start()
        GLib.timeout_add(250, self._watch_tick)

    # -- watch header auto-hide ------------------------------------------

    def _show_watch_header(self, sticky=False):
        if self._watch_header_revealer is not None:
            self._watch_header_revealer.set_reveal_child(True)
        if self._watch_toggle_btn is not None:
            self._watch_toggle_btn.set_label("\u25b4")
        self._reset_watch_header_timer(sticky)

    def _hide_watch_header(self):
        if self._watch_header_revealer is not None:
            self._watch_header_revealer.set_reveal_child(False)
        if self._watch_toggle_btn is not None:
            self._watch_toggle_btn.set_label("\u25be")
        if self._watch_header_timer is not None:
            GLib.source_remove(self._watch_header_timer)
        self._watch_header_timer = None

    def _reset_watch_header_timer(self, sticky=False):
        if self._watch_header_timer is not None:
            GLib.source_remove(self._watch_header_timer)
        self._watch_header_timer = GLib.timeout_add(
            4000 if sticky else 2200, self._on_watch_header_timeout
        )

    def _on_watch_header_timeout(self):
        self._watch_header_timer = None
        win = self._watch_window.get_window() if self._watch_window else None
        if win is not None:
            _, y, _ = win.get_pointer()
            if y < 48:
                self._reset_watch_header_timer()
                return False
        self._hide_watch_header()
        return False

    def _on_watch_hover(self, widget, event):
        if event.y < 42:
            self._show_watch_header()
        elif self._watch_header_revealer is not None and (
            self._watch_header_revealer.get_reveal_child() or self._watch_header_timer
        ):
            self._reset_watch_header_timer()
        return False

    def _on_watch_toggle(self, button):
        if self._watch_header_revealer is None:
            return
        if self._watch_header_revealer.get_reveal_child():
            self._hide_watch_header()
        else:
            self._show_watch_header(sticky=True)

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

    # -- remote control (viewer side) ------------------------------------

    def _on_request_control(self, button):
        if self._watch_sock is None or not self._watch_stream:
            return
        if self._watch_has_control:
            self._watch_has_control = False
            self._btn_control.set_label("Request Control")
            try:
                protocol.send_message(self._watch_sock, {
                    "type": protocol.CONTROL_REVOKED,
                    "stream": self._watch_stream,
                })
            except OSError:
                pass
            self._watch_msg.set_visible(False)
            return
        self._btn_control.set_sensitive(False)
        self._btn_control.set_label("Requesting…")
        try:
            protocol.send_message(self._watch_sock, {
                "type": protocol.CONTROL_REQUEST,
                "stream": self._watch_stream,
            })
        except OSError as exc:
            self._watch_control_state(False, "Failed to send control request: %s" % exc)

    def _watch_control_state(self, granted, message=None):
        self._watch_has_control = granted
        self._btn_control.set_sensitive(True)
        self._btn_control.set_label("Release Control" if granted else "Request Control")
        if self._btn_control.get_style_context().has_class("danger"):
            self._btn_control.get_style_context().remove_class("danger")
        if granted:
            self._btn_control.get_style_context().add_class("danger")
        if message and self._watch_msg is not None:
            self._watch_msg.set_text(message)
            self._watch_msg.set_visible(True)

    def _send_control_input(self, evt):
        if not self._watch_has_control or self._watch_sock is None:
            return
        try:
            protocol.send_message(self._watch_sock, {
                "type": protocol.CONTROL_INPUT,
                "stream": self._watch_stream,
                "evt": evt,
            })
        except OSError:
            pass

    def _normalize_watch_coords(self, x, y, vw, vh):
        """Convert a point in the watch widget into normalized 0..1 src coords.

        Accounts for the centre-crop done by ``_set_watch_pixbuf`` so pointer
        events land exactly on the same pixel of the host's screen even for
        non-16:9 sources.
        """
        size = self._watch_video_raw_size
        if size:
            sw, sh = size
        else:
            sw, sh = vw, vh
        if sw <= 0 or sh <= 0 or vw <= 1 or vh <= 1:
            return None
        ratio = max(vw / sw, vh / sh)
        rw, rh = sw * ratio, sh * ratio
        src_x = (x + (rw - vw) / 2.0) / rw
        src_y = (y + (rh - vh) / 2.0) / rh
        if not (0.0 <= src_x <= 1.0 and 0.0 <= src_y <= 1.0):
            return None
        return src_x, src_y

    def _on_watch_motion(self, widget, event):
        if not self._watch_has_control:
            return False
        alloc = widget.get_allocation()
        coords = self._normalize_watch_coords(event.x, event.y, alloc.width, alloc.height)
        if coords is None:
            return False
        self._send_control_input({"type": "motion", "x": coords[0], "y": coords[1]})
        return False

    def _on_watch_button(self, widget, event):
        if not self._watch_has_control:
            return False
        alloc = widget.get_allocation()
        coords = self._normalize_watch_coords(event.x, event.y, alloc.width, alloc.height)
        if coords is None:
            coords = (0.5, 0.5)
        self._send_control_input({
            "type": "button",
            "button": event.button,
            "pressed": True,
            "x": coords[0],
            "y": coords[1],
        })
        return True

    def _on_watch_button_release(self, widget, event):
        if not self._watch_has_control:
            return False
        self._send_control_input({
            "type": "button",
            "button": event.button,
            "pressed": False,
        })
        return True

    def _on_watch_scroll(self, widget, event):
        if not self._watch_has_control:
            return False
        direction = event.direction
        button = None
        if direction == Gdk.ScrollDirection.UP:
            button = 4
        elif direction == Gdk.ScrollDirection.DOWN:
            button = 5
        elif direction == Gdk.ScrollDirection.SMOOTH:
            if event.delta_y < 0:
                button = 4
            elif event.delta_y > 0:
                button = 5
        if button:
            self._send_control_input({"type": "scroll", "button": button})
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
            self._watch_sock = sock
            self._ui_update(self._enable_control_button)
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
                msg_type = header.get("type")
                if msg_type == protocol.ERROR:
                    self._ui_update(self._watch_fail, "Stream not available: %s"
                                    % header.get("reason", "unknown"))
                    break
                if msg_type == protocol.CONTROL_GRANTED:
                    self._ui_update(self._watch_control_state, True)
                    continue
                if msg_type == protocol.CONTROL_DENIED:
                    self._ui_update(
                        self._watch_control_state, False,
                        "Control request was denied by the host.",
                    )
                    continue
                if msg_type == protocol.CONTROL_REVOKED:
                    self._ui_update(
                        self._watch_control_state, False,
                        "Control was revoked by the host.",
                    )
                    continue
                if msg_type == protocol.FRAME and payload:
                    now = time.monotonic()
                    self._watch_last_frame_time = now
                    self._watch_frame_times.append(now)
                    if len(self._watch_frame_times) > 30:
                        self._watch_frame_times = self._watch_frame_times[-30:]
                    pixbuf = self._jpeg_to_pixbuf(payload)
                    if pixbuf is not None:
                        if self._watch_video_raw_size is None:
                            self._watch_video_raw_size = (
                                pixbuf.get_width(),
                                pixbuf.get_height(),
                            )
                        GLib.idle_add(self._set_watch_pixbuf, pixbuf)
        except (OSError, protocol.ProtocolError):
            self._ui_update(self._watch_fail, "Lost connection to the relay at %s:%s"
                            % (self.server, self.port))
        finally:
            if self._watch_has_control:
                self._watch_has_control = False
            self._watch_sock = None
            if sock is not None:
                sock.close()

    def _enable_control_button(self):
        if self._btn_control is not None:
            self._btn_control.set_sensitive(True)

    def _watch_tick(self):
        if self._watch_stop.is_set() or self._watch_window is None:
            return False
        now = time.monotonic()
        if self._watch_last_frame_time is not None:
            age = now - self._watch_last_frame_time
            if self._watch_age_label is not None:
                self._watch_age_label.set_text("%.1fs" % age)
            if self._watch_live_dot is not None:
                ctx = self._watch_live_dot.get_style_context()
                for cls in ("dot-ok", "dot-err"):
                    ctx.remove_class(cls)
                ctx.add_class("dot-ok" if age < 3.0 else "dot-err")
        else:
            if self._watch_age_label is not None:
                self._watch_age_label.set_text("no frames yet")
            if self._watch_live_dot is not None:
                ctx = self._watch_live_dot.get_style_context()
                ctx.remove_class("dot-ok"); ctx.add_class("dot-err")
        times = list(self._watch_frame_times)
        if len(times) >= 2:
            span = times[-1] - times[0]
            fps = (len(times) - 1) / span if span > 0 else 0.0
        else:
            fps = 0.0
        if self._watch_fps_label is not None:
            self._watch_fps_label.set_text("%.0f fps" % fps)
        return True

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
        if self._watch_has_control and self._watch_sock is not None:
            try:
                protocol.send_message(self._watch_sock, {
                    "type": protocol.CONTROL_REVOKED,
                    "stream": self._watch_stream or "",
                })
            except OSError:
                pass
        self._watch_has_control = False
        self._watch_sock = None
        self._watch_stream = None
        self._watch_video_raw_size = None
        if self._watch_header_timer is not None:
            GLib.source_remove(self._watch_header_timer)
            self._watch_header_timer = None
        if self._watch_window is not None:
            self._watch_window.destroy()
            self._watch_window = None
            self._watch_image = None
            self._watch_msg = None
            self._watch_live_dot = None
            self._btn_control = None
            self._watch_eventbox = None
            self._watch_header_revealer = None
            self._watch_toggle_btn = None
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
