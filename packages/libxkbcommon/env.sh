# Keymaps: turns evdev keycodes into keysyms and text, for the compositor and every
# client. The keymap data itself is xkeyboard-config, read at run time.
VERSION="1.13.2"
PACKAGE="libxkbcommon-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/xkbcommon/libxkbcommon/archive/refs/tags/xkbcommon-${VERSION}.tar.gz"
SHA256="acc4d5f7c3cbba5f9f8d08d8bdbeede84ecede46792f47929aa9321873385528"
# The library's part of its LICENSE, which names each by SPDX identifier.
LICENSE="MIT AND MIT-open-group AND HPND AND HPND-sell-variant AND HPND-DEC"
CPE="cpe:2.3:a:xkbcommon:libxkbcommon:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="xkbcommon/libxkbcommon"
UPSTREAM_SED='s/^xkbcommon-//'
EXTENSION=kiosk
