# HTTP/2 framing for libsoup. Only the library; the tools want more than it does.
VERSION="1.71.0"
PACKAGE="nghttp2-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://github.com/nghttp2/nghttp2/releases/download/v${VERSION}/${TARBALL}"
SHA256="c0f55121d88650e4b9227aec28cebaf664d2417ca60fe70be0f8d38829f52116"
LICENSE="MIT"
CPE="cpe:2.3:a:nghttp2:nghttp2:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="nghttp2/nghttp2"
EXTENSION=kiosk
