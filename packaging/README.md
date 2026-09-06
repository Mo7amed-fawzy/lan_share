# Lan-Share .deb packaging

Turn this project into a `.deb` that any Debian/Ubuntu machine can install,
upgrade and uninstall from the terminal, exactly like an app from their normal
repositories.

## Layout

```
packaging/
  VERSION                 single source of truth for the package version
  DEBIAN/control          package metadata + dependencies
  DEBIAN/postinst         refresh desktop/icon caches, reload systemd
  DEBIAN/prerm            stop+disable the relay only on full removal
  DEBIAN/postrm           cleanup on purge
  usr/bin/                launchers: lan-share, lan-share-gui,
                          lan-share-relay, lan-share-server
  usr/share/applications/ desktop entries for the app menu
  usr/lib/systemd/system/ lan-share-relay.service (run the relay on boot)
  build_deb.sh            build the .deb (needs only dpkg-dev)
  make_apt_repo.sh        index .deb files for apt update/upgrade
```

## Build

```bash
sudo apt install dpkg-dev        # on the build machine (once)
./packaging/build_deb.sh
```

Produces `packaging/dist/lan-share_<version>_all.deb`.

## Install / upgrade / remove

```bash
# first install (or any upgrade with a newer .deb)
sudo apt install ./packaging/dist/lan-share_1.0.0_all.deb

# upgrading later with a newer file just works - apt handles it
sudo apt install ./packaging/dist/lan-share_1.0.1_all.deb

# uninstall
sudo apt remove lan-share        # keep config
sudo apt purge lan-share         # remove everything
```

Everything is installed from the app menu:

- **Lan-Share**  - native GTK3 desktop app
- **Lan-Share Relay** - relay control window
- `systemctl enable --now lan-share-relay.service` runs the relay at boot
  (required only on the PC that acts as the server).

## Keeping clients updated from one machine (apt repository)

The simplest way to push new versions to every PC on the LAN:

1. Build and collect `.deb` files in one directory:
   ```bash
   ./packaging/build_deb.sh
   ./packaging/make_apt_repo.sh dist
   ```
2. Serve that directory over HTTP (any always-on PC on the LAN):
   ```bash
   cd packaging/dist
   python3 -m http.server 8080 --directory . &
   ```
3. Point each client at it once:
   ```bash
   echo 'deb [trusted=yes] http://<server-ip>:8080/ ./' \
      | sudo tee /etc/apt/sources.list.d/lan-share.list
   sudo apt update
   sudo apt install lan-share       # or later: sudo apt upgrade
   ```
   Now `sudo apt update && sudo apt upgrade` updates Lan-Share with the rest
   of the system. `[trusted=yes]` is fine on a trusted LAN; for a public repo
   you should sign it (add a GPG-signed `Release` file) and drop the flag.

## Bumping the version

Only edit `packaging/VERSION` (e.g. `1.0.1`), rebuild, and the new `.deb` will
be seen as an upgrade of the old one by apt.

## Notes

- The package hard-depends on Debian/Ubuntu system packages
  (`python3-pil`, `python3-gi`, `gir1.2-gtk-3.0`, `python3-tk`), so there is
  no vendored Python and no virtualenv inside the `.deb`.
- Code is installed under `/usr/lib/lan-share`; the `/usr/bin/*` launchers
  `cd` there, so `client/main.py` auto-discovery and the `scripts/*.sh`
  helpers keep working from the app menu or a terminal.
- The relay uses UDP 8502 for discovery. With UFW active, open it on the
  server PC:
  `sudo ufw allow 8501/tcp && sudo ufw allow 8502/udp`