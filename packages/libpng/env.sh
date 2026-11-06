# PNG decoding, for the browser's images and freetype's colour emoji bitmaps.
VERSION="1.6.59"
PACKAGE="libpng-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://download.sourceforge.net/libpng/${TARBALL}"
SHA256="d80dd2a38a37f803cb9b6ac7b14bd6e74ddc3b654780a8380bdf93523fdb4389"
LICENSE="libpng-2.0"
CPE="cpe:2.3:a:libpng:libpng:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="pnggroup/libpng"
# 1.7 has been in beta for a decade; 1.6 is the maintained series.
UPSTREAM_IGNORE='.*beta.*|1\.[7-9]\..*'
EXTENSION=kiosk
