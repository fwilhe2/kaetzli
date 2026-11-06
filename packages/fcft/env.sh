# Font loading and glyph rendering into pixman images: foot's text renderer, by foot's
# author. Its SVG glyph support is the bundled nanosvg (two C files).
VERSION="3.3.3"
PACKAGE="fcft-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://codeberg.org/dnkl/fcft/archive/${VERSION}.tar.gz"
SHA256="b0c0f4a599f43723736c8565b8b84337c4195077f07f1bb8bb3252bb13a2306a"
LICENSE="MIT AND Zlib"
UPSTREAM_INDEX="https://codeberg.org/dnkl/fcft/tags.rss"
UPSTREAM_REGEX='<title>([0-9]+\.[0-9]+\.[0-9]+)</title>'
EXTENSION=kiosk
DEPENDS="tllist fontconfig freetype pixman"
