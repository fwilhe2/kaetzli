# The kiosk's font: Adwaita Mono, GNOME's monospace face (from Iosevka). Only the mono
# files are installed: Debian ships this release with the two Sans files removed
# (fonts-adwaita, Files-Excluded) and the mono ones unchanged, and a terminal needs mono.
# Fonts for a browser are their own decision (docs/proposals/kiosk.md).
VERSION="51.0"
PACKAGE="adwaita-fonts-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://download.gnome.org/sources/adwaita-fonts/${VERSION%%.*}/${TARBALL}"
SHA256="fa104ae2c1b96580d322f563ffe8b2dfca5296b0eca4971efd2d8011405123d2"
LICENSE="OFL-1.1"
UPSTREAM_INDEX="https://download.gnome.org/sources/adwaita-fonts/"
UPSTREAM_SUBDIR="[0-9]+/"
EXTENSION=kiosk
