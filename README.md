# Lan-Share

A Linux screen sharing application for LAN devices — inspired by the Flutter
`bundle` app (`com.screenshare.screen_share`). It captures a screen and streams
it as JPEG frames over a TCP relay so any device on the same network can watch
it.

## Architecture

```
Lan-Share/
├── core/          # shared library (protocol, capture, player)
├── server/        # the relay hub - START/RUN THIS FIRST
├── client/        # the app - share your screen or watch others
│   ├── main.py    # CLI: share / view
│   ├── desktop.py # GTK3 desktop app (old-bundle look, lists sessions)
│   ├── manager.py # browser-based GUI control panel
│   └── web/       # GUI pages (HTML/JS served by manager.py)
├── config/        # JSON settings for server and client
└── scripts/       # install + launcher scripts
```

- **Server directory** (`server/`): one machine runs `server/main.py`, which
  accepts sharers and relays their frames to viewers on the LAN.
- **Client directory** (`client/`): each machine runs `client/main.py`. A
  client can **share** its screen to the network or **view** someone else's
  shared screen.

## Requirements

- Python 3.10+
- Pillow (`python3 -m pip install -r requirements.txt`)
- `python3-tk` for the viewer window (`sudo apt-get install python3-tk`)
- X11 session for screen capture (`PIL.ImageGrab`)

## Quick start

```bash
# 1. one-time setup on each machine (deps + firewall port)
./scripts/setup_lan.sh

# 2. on the server machine, start the relay (all interfaces)
./scripts/run_server.sh --host 0.0.0.0 --port 8501

# 3. on any machine, share this screen
./scripts/run_client.sh --server <SERVER_IP> --port 8501 --stream desk --fps 10

# 4. on any other machine, watch the stream
./scripts/run_viewer.sh --server <SERVER_IP> --port 8501 --stream desk
```

Every launcher script just calls `python3 -m ...`, so you can run the modules
directly from the Lan-Share folder instead (no wrapper scripts needed):

```bash
python3 -m server.main --host 0.0.0.0 --port 8501        # relay
python3 -m client.desktop --server <SERVER_IP> --port 8501   # GTK3 desktop app
python3 -m client.manager --open                             # browser GUI
python3 -m client.main share --server <SERVER_IP> --stream desk
python3 -m client.main view  --server <SERVER_IP> --stream desk
```

## Desktop app

`./scripts/run_desktop.sh` opens a native GTK3 window styled after the old
`bundle` app. It shows the sessions currently shared on the relay, lets you
share this screen under any session name (defaults to the PC host name), and
opens a live viewer window for any session. Requires GTK3 (PyGObject).

## GUI manager

`./scripts/run_gui.sh` starts a local web control panel on
`http://127.0.0.1:8701` (opens your browser automatically). From one page you
can:

- set the relay server IP / port
- start and stop sharing this screen (stream name, fps, quality, scale)
- list the streams currently available on the relay
- watch any stream live in the browser (MJPEG) - no tkinter required

**Server machine:** `./scripts/run_server.sh --host 0.0.0.0`
**Client machine:** `./scripts/run_gui.sh` → set the server IP → Start sharing,
or refresh streams and click **Watch**.

## Usage

Server:

```bash
python3 -m server.main --host 0.0.0.0 --port 8501
```

Client (share):

```bash
python3 -m client.main share --server <SERVER_IP> --stream desk --fps 10 --scale 0.5
```

Client (view):

```bash
python3 -m client.main view --server <SERVER_IP> --stream desk
```

## Options

| Flag        | Default            | Meaning                              |
|-------------|--------------------|--------------------------------------|
| `--server`  | `127.0.0.1`        | relay server IP                      |
| `--port`    | `8501`             | relay TCP port                       |
| `--stream`  | hostname           | stream name                          |
| `--fps`     | `10.0`             | capture frame rate                   |
| `--quality` | `80`               | JPEG quality (1-100)                 |
| `--scale`   | `0.5`              | downscale factor for performance     |
| `--region`  | full screen        | `X Y W H` capture region             |

## How it works

1. A **sharer** connects and registers a stream name.
2. A **viewer** connects, lists streams, and subscribes to one.
3. The **server** keeps the latest JPEG frame per stream and forwards new
   frames to every subscriber.
