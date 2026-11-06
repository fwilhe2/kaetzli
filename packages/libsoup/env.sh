# HTTP for WebKit: HTTP/1.1 and HTTP/2, cookies, caching, WebSockets. TLS is GIO's, from
# glib-networking.
VERSION="3.8.1"
PACKAGE="libsoup-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://download.gnome.org/sources/libsoup/${VERSION%.*}/${TARBALL}"
SHA256="5013dcef9136f6f861e6529fca578af50c1f52a39219027fb5aff652d1371a04"
LICENSE="LGPL-2.0-or-later"
CPE="cpe:2.3:a:gnome:libsoup:${VERSION}:*:*:*:*:*:*:*"
EXTENSION=kiosk
DEPENDS="glib-libs sqlite nghttp2 libpsl brotli"
