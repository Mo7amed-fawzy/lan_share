# Lan-Share Client

The client app. A client machine can share its own screen to the LAN, or watch
a screen shared by another machine.

## Desktop app (recommended)

Native GTK3 window that mirrors the look of the old Flutter `bundle` app
(`com.screenshare.screen_share`) and works with the current relay server setup.
It lists the sessions on the relay, lets you share this screen under a session
name (defaults to the PC host name), and opens a live viewer for any session.

```bash
./scripts/run_desktop.sh --server <SERVER_IP> --port 8501
```

Requires GTK3 / PyGObject:

```bash
sudo apt-get install python3-gi python3-gi-cairo gir1.2-gtk-3.0
```

Session names default to your PC host name; type a different name to override
it. The "Start local relay" button launches the relay server on this machine
for quick testing.

## Share your screen

```bash
./scripts/run_client.sh --server <SERVER_IP> --port 8501 --stream desk
```

or directly:

```bash
python3 -m client.main share --server <SERVER_IP> --stream desk --fps 10
```

## Watch someone's screen

```bash
./scripts/run_viewer.sh --server <SERVER_IP> --port 8501 --stream desk
```

Requires `python3-tk` for the viewer window:

```bash
sudo apt-get install python3-tk
```

If tkinter is missing, the client still decodes frames (headless mode) but no
window is shown.

## GUI manager (recommended)

The browser-based control panel needs **no tkinter** and no extra packages:

```bash
./scripts/run_gui.sh
# opens http://127.0.0.1:8701 automatically
```

From the page you can configure the relay server, start/stop sharing this
screen, list the streams on the relay, and watch any stream live in the
browser (MJPEG). Direct usage:

```bash
python3 -m client.manager --port 8701 --open
```
