"""Shared Lan-Share colour tokens (dark + light) and the GTK CSS built from
them.

The web UI (``client/web/index.html`` / ``watch.html``) uses the exact same
tokens as CSS custom properties, so the GTK app and the browser control panel
always look like one product:

    dark  = the default "midnight" palette
    light = a clean light counterpart selected via the header toggle

Python-side consumers import :func:`gtk_css` (e.g. ``client/desktop.py``).
Web-side palettes are kept in sync manually in the ``:root`` blocks.
"""

DARK = {
    "bg": "#2d2d3f",
    "surface": "#1e1e2e",
    "card": "#26263a",
    "pill": "#33344a",
    "border": "#3a3a54",
    "text": "#94a3b8",
    "text-strong": "#b9c4d4",
    "muted": "#8592a6",
    "accent": "#505a76",
    "accent-strong": "#66718f",
    "ok": "#46d17b",
    "ok-dim": "#3d6b52",
    "err": "#e5736e",
    "err-dim": "#6b3a44",
    "err-border": "#8a4a56",
}

LIGHT = {
    "bg": "#eef1f6",
    "surface": "#ffffff",
    "card": "#f8fafc",
    "pill": "#e7ebf2",
    "border": "#d3dae5",
    "text": "#4a5568",
    "text-strong": "#1f2937",
    "muted": "#6b7488",
    "accent": "#aab3c8",
    "accent-strong": "#64748b",
    "ok": "#1f9d57",
    "ok-dim": "#cfeede",
    "err": "#c94f4f",
    "err-dim": "#f5d9d9",
    "err-border": "#e0a3a3",
}


def gtk_css(p):
    """Return the Gtk CSS for a palette dict (DARK or LIGHT)."""
    return """
window { background-color: %(bg)s; color: %(text)s; }
.header { background-color: %(surface)s; border-bottom: 1px solid %(border)s; }
.brand-title { font-size: 15px; font-weight: 600; color: %(text-strong)s; }
.brand-sub { font-size: 11px; color: %(muted)s; }
.pill {
    background-color: %(pill)s; border: 1px solid %(border)s;
    border-radius: 999px; padding: 4px 12px; font-size: 12px; color: %(text)s;
}
.pill .dot { min-width: 8px; min-height: 8px; border-radius: 4px; background-color: %(muted)s; }
.dot-ok { background-color: %(ok)s; }
.dot-err { background-color: %(err)s; }
.content { background-color: %(bg)s; padding: 16px 20px; }
.hero-title { font-size: 20px; font-weight: 600; color: %(text)s; }
.hero-sub { font-size: 13px; color: %(muted)s; }
.meta { font-size: 11px; color: %(muted)s; }
.meta-ok { color: %(ok)s; }
.meta-err { color: %(err)s; }
.card { background-color: %(card)s; border: 1px solid %(border)s; border-radius: 12px; padding: 14px 16px; }
.card-title {
    font-size: 11px; font-weight: 600; letter-spacing: 1px; color: %(muted)s;
}
.field { font-size: 11px; color: %(muted)s; }
.segmented { background-color: %(surface)s; border: 1px solid %(border)s; border-radius: 999px; padding: 2px; }
.segmented button.seg {
    background-color: transparent; background-image: none; border: none;
    box-shadow: none; border-radius: 999px; color: %(muted)s; padding: 4px 0;
}
.segmented button.seg:checked {
    background-color: %(accent)s; color: #ffffff; font-weight: 500;
}
scale trough {
    background-color: %(pill)s; border-radius: 4px; min-height: 6px;
}
scale highlight { background-color: %(accent-strong)s; border-radius: 4px; }
scale slider {
    background-color: %(text-strong)s; border: none; border-radius: 50%%;
    min-width: 14px; min-height: 14px;
}
#quality-label { font-size: 12px; color: %(text-strong)s; }
.session-card {
    background-color: %(surface)s; border: 1px solid %(border)s;
    border-radius: 10px; padding: 8px 10px;
}
.session-card:hover { border-color: %(accent-strong)s; }
.session-name { font-size: 14px; font-weight: 600; color: %(text-strong)s; }
.session-host { font-size: 11px; color: %(muted)s; }
.session-list { background-color: transparent; }
button.watch-btn { padding: 4px 12px; font-size: 12px; }
entry, spinbutton {
    background-color: %(surface)s; background-image: none;
    border: 1px solid %(border)s; border-radius: 10px;
    color: %(text-strong)s; padding: 6px 10px;
}
entry:focus, spinbutton:focus { border-color: %(accent-strong)s; }
combobox button {
    background-color: %(surface)s; background-image: none;
    border: 1px solid %(border)s; border-radius: 10px; color: %(text-strong)s;
}
button {
    background-color: %(pill)s; background-image: none;
    border: 1px solid %(border)s; border-radius: 999px;
    color: %(text)s; padding: 6px 16px;
}
button:hover { background-color: %(accent)s; color: #ffffff; border-color: %(accent-strong)s; }
button.primary { background-color: %(accent)s; border-color: %(accent-strong)s; color: #ffffff; font-weight: 500; }
button.small { padding: 3px 10px; font-size: 11px; }
button.danger { background-color: %(err-dim)s; border-color: %(err-border)s; color: %(err)s; }
button.danger:hover { background-color: %(err)s; border-color: %(err)s; color: #ffffff; }
button:disabled { opacity: 0.45; }
.list-row { background-color: %(surface)s; border-bottom: 1px solid %(border)s; }
.list-row:hover { background-color: %(card)s; }
#share-status { font-size: 12px; color: %(muted)s; }
#status-bar { background-color: %(surface)s; color: %(muted)s; font-size: 11px; padding: 6px 14px; }
#stream-empty { font-size: 13px; color: %(muted)s; }
#watch-title { font-size: 14px; color: %(text-strong)s; }
.watch-bar { background-color: %(surface)s; border-bottom: 1px solid %(border)s; }
button.watch-toggle {
    background-color: %(surface)s; border: 1px solid %(border)s;
    border-radius: 999px; padding: 2px 8px; font-size: 10px;
    color: %(muted)s; opacity: 0.85;
}
button.watch-toggle:hover { background-color: %(accent)s; color: #ffffff; }
""" % p