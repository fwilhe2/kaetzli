# Brotli decompression: WOFF2 web fonts (through freetype) and HTTP's br content
# encoding (through libsoup), both for the kiosk extension's browser.
VERSION="1.2.1"
PACKAGE="brotli-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/google/brotli/archive/refs/tags/v${VERSION}.tar.gz"
SHA256="98d35d11a78dc453aebb7d10c3748f3f73ff78c8267c0dc697b8631f67bf7e8f"
LICENSE="MIT"
CPE="cpe:2.3:a:google:brotli:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="google/brotli"
EXTENSION=kiosk
