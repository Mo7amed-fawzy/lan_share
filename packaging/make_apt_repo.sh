#!/usr/bin/env bash
# Turn the built .deb files into a minimal apt repository, so every client can
# keep Lan-Share up to date with plain:
#
#     sudo apt update && sudo apt upgrade   (or: sudo apt install lan-share)
#
# Usage:
#     ./make_apt_repo.sh                    # index dist/*.deb in-place
#     ./make_apt_repo.sh /path/to/repo      # copy deb into a repo directory
#
# Then host that directory over HTTP/HTTPS (nginx, python3 -m http.server,
# or copy it to an always-on machine on the LAN). On each client:
#
#     echo 'deb [trusted=yes] http://<host>/lan-share ./' \
#        | sudo tee /etc/apt/sources.list.d/lan-share.list
#     sudo apt update
#     sudo apt install lan-share
#
# NOTE: [trusted=yes] skips GPG verification. For anything beyond a home LAN,
# sign the repo (add a Release file with a GPG signature) and drop the flag.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

case "${1:-}" in
    /*) REPO_DIR="${1:-$HERE/dist}" ;;
    *)  REPO_DIR="$HERE/${1:-dist}" ;;
esac
mkdir -p "$REPO_DIR"
cd "$REPO_DIR"

if ! ls *.deb >/dev/null 2>&1; then
    echo "No .deb files in $REPO_DIR - run ./build_deb.sh first." >&2
    exit 1
fi

dpkg-scanpackages . /dev/null | gzip -9c > Packages.gz
dpkg-scanpackages . /dev/null > Packages

echo "== apt repository ready: $(pwd) =="
echo "  Make this directory reachable over HTTP, then on clients:"
echo "    echo 'deb [trusted=yes] http://<server-ip>/lan-share ./' \\"
echo "       | sudo tee /etc/apt/sources.list.d/lan-share.list"
echo "    sudo apt update && sudo apt install lan-share"