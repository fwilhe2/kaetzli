# WPE WebKit, WebKit's port for embedded browsers: no toolkit, its own platform library
# for Wayland. Ships the engine and its MiniBrowser, the kiosk's browser for now
# (docs/proposals/kiosk.md, *The spike*).
VERSION="2.54.2"
PACKAGE="wpewebkit-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://wpewebkit.org/releases/${TARBALL}"
SHA256="f3284e19e47fcb5ac8c63c2e1c113dbd65b2423eb8a545f5b345707829004f56"
LICENSE="BSD-2-Clause AND BSD-3-Clause AND LGPL-2.0-or-later AND LGPL-2.1-or-later AND Apache-2.0"
CPE="cpe:2.3:a:wpewebkit:wpe_webkit:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_INDEX="https://wpewebkit.org/releases/"
UPSTREAM_REGEX='wpewebkit-([0-9]+\.[0-9]*[02468]\.[0-9]+)\.tar\.xz'
EXTENSION=kiosk
DEPENDS="glib-libs icu harfbuzz freetype fontconfig libjpeg-turbo libpng libwebp libepoxy mesa libgcrypt libtasn1 libxml2 sqlite libsoup libdrm wayland wayland-protocols libxkbcommon brotli systemd"
