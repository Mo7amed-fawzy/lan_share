# Lan-Share Server

The relay hub. Start this on one machine that is reachable by all other
devices on the LAN.

## Start

```bash
./scripts/run_server.sh --host 0.0.0.0 --port 8501
```

or directly:

```bash
python3 -m server.main --host 0.0.0.0 --port 8501
```

## Behavior

- Accepts screen **sharers** and registers their stream names.
- Accepts **viewers**, lists active streams, and relays each sharer's latest
  frame to its subscribers.
- Unregisters streams and viewers automatically on disconnect.
