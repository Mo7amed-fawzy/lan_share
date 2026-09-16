---
name: lan-share-dev
description: Use ONLY when editing, debugging, testing, packaging, or shipping the Lan-Share LAN screen-sharing app in this repo (core/, client/, server/, packaging/). Covers the wire protocol, the GTK3 desktop app, the browser control panel (index.html/watch.html), region/window capture, the .deb build, and the LAN apt repo.
---

# Developing Lan-Share

Lan-Share captures an X11 screen, encodes JPEG frames, and streams them over a
TCP relay so any LAN device can watch. It is pure Python + GTK3 + a tiny
HTTP/HTML client. No build step and no framework — edit and re-run.

## Architecture / where to change things

| Concern | File |
| --- | --- |
| Wire protocol, framing, message types | `core/protocol.py` |
| Screen capture + JPEG encode, `region` support | `core/capture.py` |
| tkinter viewer (HeadlessPlayer fallback) | `core/player.py` |
| UDP LAN discovery | `core/discovery.py` |
| PING/PONG health checks | `core/health.py` |
| Relay hub (sharers -> viewers, stream metadata) | `server/main.py` |
| CLI `share` / `view` / `health` | `client/main.py` |
| GTK3 desktop app (primary UI) | `client/desktop.py` |
| Browser control panel + `/api/*` + `/stream/*` | `client/manager.py` |
| Web control page | `client/web/index.html` |
| Web watch page | `client/web/watch.html` |
| Shared DARK/LIGHT palette + GTK CSS builder | `client/theme.py` |
| Relay control GTK window | `client/relay_control.py` |
| `.deb` build + LAN apt repo | `packaging/` |

**Important:** the GTK palette in `client/theme.py` (`DARK`/`LIGHT` dicts) and
the CSS custom properties in `client/web/index.html` + `client/web/watch.html`
are the same design tokens, kept in sync manually. If you change a colour, change
it in all three.

## Wire protocol (`core/protocol.py`)

Every message: `[4-byte big-endian length][JSON header][optional payload]`.
Frame payloads are raw JPEG; `header["size"]` holds the payload byte count.

Message flow:
- Sharer: `SHARER_HELLO` -> wait `SHARER_REGISTERED` -> loop `FRAME` -> `BYE`.
- Viewer: `STREAM_LIST` -> `STREAM_LIST_RESP` -> `SUBSCRIBE` -> wait
  `SUBSCRIBED` -> receive `FRAME`s.
- Errors: `ERROR` with `reason` in `UNKNOWN_STREAM` / `NOT_SHARER` /
  `INVALID_MESSAGE`.
- Health: `PING` -> `PONG`.

Relay ports: TCP `8501` (streams), UDP `8502` (discovery). Web control panel
defaults to `127.0.0.1:8701`.

`STREAM_LIST_RESP` carries per-stream metadata used by all UIs:
`streams`, `viewer_counts`, `last_frame_age`, `uptime` (all keyed by stream
name). If you add a field, populate it in `server/main.py` and consume it in
`client/desktop.py` `list_streams()` and `client/manager.py` `list_streams()`.

## Running the pieces

```bash
# relay (one machine, bind all interfaces)
python3 -m server.main --host 0.0.0.0 --port 8501

# GTK3 desktop app
python3 -m client.desktop --server <SERVER_IP> --port 8501

# browser control panel (served at http://127.0.0.1:8701)
python3 -m client.manager --open

# CLI share / view / health
python3 -m client.main share --server <SERVER_IP> --stream desk --fps 10 --region 0 0 1600 900
python3 -m client.main view  --server <SERVER_IP> --stream desk
python3 -m client.main health --server <SERVER_IP> --stream desk
```

`--server` omitted triggers UDP auto-discovery. Serve `client/web/*` through
`manager.py` so browser pages can reach `/api/*` and `/stream/*`.

## Testing

```bash
python3 -m unittest discover -s tests      # integration tests (tests/test_relay.py)
python3 -m py_compile client/*.py server/*.py core/*.py   # fast syntax check
```

The integration tests spin up a real relay and exercise share/view/metadata.
Run them after any protocol or `server/main.py` change. Always run
`py_compile` after editing GTK/manager code (they import GTK at module load,
so tests may not cover them).

## Packaging & shipping

Only `packaging/VERSION` controls the version string. Bump it, then:

```bash
./packaging/build_deb.sh                    # -> packaging/dist/lan-share_<version>_all.deb
./packaging/make_apt_repo.sh                # re-index dist/*.deb for apt
sudo apt install ./packaging/dist/lan-share_<version>_all.deb   # first install
sudo apt install ./packaging/dist/lan-share_<version>_all.deb   # upgrade
```

The `.deb` installs source to `/usr/lib/lan-share` and launchers to
`/usr/bin/lan-share{,-gui,-relay,-server}`. Two systemd units ship:
`lan-share-relay.service` (the relay) and `lan-share-repo.service` (serves the
apt repo on port 8080 for LAN clients).

Clients join the LAN repo once:
```bash
echo 'deb [trusted=yes] http://<SERVER_IP>:8080 ./' | sudo tee /etc/apt/sources.list.d/lan-share.list
sudo apt update && sudo apt install lan-share
```
After that, updates are `sudo apt update && sudo apt upgrade`.

**Gotcha:** a running relay keeps the old code until restarted. After
reinstalling the `.deb`, run `sudo systemctl restart lan-share-relay.service`,
otherwise new protocol fields (e.g. `viewer_counts`) never reach clients.

**Gotcha:** clients may still have an old source checkout at `~/Lan-Share` and
launch that instead of `/usr/lib/lan-share`. If the UI looks stale, check with
`dpkg -l lan-share` and remove `~/Lan-Share`.

Firewall: `sudo ufw allow 8501/tcp && sudo ufw allow 8502/udp`; for the apt
repo also `sudo ufw allow 8080/tcp`.

## Conventions

- No comments unless non-obvious; match surrounding style.
- UI changes must stay consistent across the GTK app (`client/desktop.py`),
  the desktop theme (`client/theme.py`), and both web pages.
- Web rendering must be XSS-safe: build DOM with `textContent` /
  `createElement`, never `innerHTML` with stream names.
- Keep `[trusted=yes]` documented as LAN-only (repo is unsigned).
- Never commit secrets; `packaging/build/` and `packaging/dist/` are
  git-ignored.
