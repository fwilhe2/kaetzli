# Text shaping: which glyphs, in which positions, a run of text becomes. WebKit's text
# rendering, with its ICU integration.
VERSION="15.1.0"
PACKAGE="harfbuzz-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://github.com/harfbuzz/harfbuzz/releases/download/${VERSION}/${TARBALL}"
SHA256="0fc7c565443a5f4be7da65ac5ee5bd737d9b158dc908708b7689a0a5796b0c8d"
LICENSE="MIT-Modern-Variant"
CPE="cpe:2.3:a:harfbuzz_project:harfbuzz:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="harfbuzz/harfbuzz"
EXTENSION=kiosk
DEPENDS="freetype icu"
