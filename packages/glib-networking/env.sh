# GIO's TLS backend, a module libgio loads: without it libsoup can open no https://
# connection. The OpenSSL backend, against the base's OpenSSL and its trust store.
VERSION="2.90.1"
PACKAGE="glib-networking-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://download.gnome.org/sources/glib-networking/${VERSION%.*}/${TARBALL}"
SHA256="ea95636590098355a05e3027579dba1a9a950b05e456b2488ea03d82e4b6c18a"
LICENSE="LGPL-2.1-or-later"
CPE="cpe:2.3:a:gnome:glib-networking:${VERSION}:*:*:*:*:*:*:*"
EXTENSION=kiosk
DEPENDS="glib-libs openssl"
