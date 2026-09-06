#!/usr/bin/env bash
# Build a Lan-Share .deb package with plain dpkg-deb (no debhelper needed).
#
#   ./build_deb.sh                -> ../dist ?
#
# Produces: dist/lan-share_<version>_all.deb
#
# The output .deb can be installed/upgraded with apt:
#
#     sudo apt install ./dist/lan-share_<version>_all.deb
#     sudo apt install ./dist/lan-share_<version>_all.deb   # again = upgrade
set -euo pipefail
cd "$(dirname "$0")"

VERSION="$(cat VERSION)"
PKG="lan-share"
ROOT="$(cd .. && pwd)"
STAGE="build"
OUT_DIR="dist"
DEB="$OUT_DIR/${PKG}_${VERSION}_all.deb"
LIBDIR="usr/lib/${PKG}"

echo "== Building $PKG $VERSION .deb =="

rm -rf "$STAGE"
mkdir -p "$STAGE" "$OUT_DIR"

# 1. Static packaging files (control scripts, wrappers, desktop entries, unit).
cp -a DEBIAN "$STAGE/"
cp -a usr "$STAGE/"

# 2. Core application source -> /usr/lib/lan-share.
mkdir -p "$STAGE/$LIBDIR"
for dir in client core server config scripts; do
    cp -a "$ROOT/$dir" "$STAGE/$LIBDIR/"
done
find "$STAGE/$LIBDIR" -name __pycache__ -type d -prune -exec rm -rf {} \;
find "$STAGE/$LIBDIR" -name '*.pyc' -delete
find "$STAGE/$LIBDIR" -name bin -type d -prune -exec rm -rf {} \;
chmod +x "$STAGE/$LIBDIR"/scripts/*.sh

# 3. Icons into the hicolor theme (256x256 as shipped in client/).
ICONS="$STAGE/usr/share/icons/hicolor"
mkdir -p "$ICONS/256x256/apps"
cp -a "$ROOT/client/lan_share.png"        "$ICONS/256x256/apps/lan-share.png"
cp -a "$ROOT/client/lan_share_server.png" "$ICONS/256x256/apps/lan-share-server.png"

# 4. Keep the Version in control and DEBIAN in sync.
sed -i "s/^Version: .*/Version: ${VERSION}-1/" "$STAGE/DEBIAN/control"

# 5. Build.
dpkg-deb --build --root-owner-group "$STAGE" "$DEB" >/dev/null

echo "== Built $DEB =="
echo
echo "  Install / upgrade:   sudo apt install ./$DEB"
echo "  Remove:              sudo apt remove lan-share"
echo "  Relay as a service:  sudo systemctl enable --now lan-share-relay.service"