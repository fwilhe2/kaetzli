# GL and EGL function pointer management: WebKit calls EGL through it, and it opens
# mesa's libEGL at run time.
VERSION="1.5.10"
PACKAGE="libepoxy-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/anholt/libepoxy/archive/refs/tags/${VERSION}.tar.gz"
SHA256="a7ced37f4102b745ac86d6a70a9da399cc139ff168ba6b8002b4d8d43c900c15"
LICENSE="MIT"
UPSTREAM_GITHUB="anholt/libepoxy"
EXTENSION=kiosk
DEPENDS="mesa"
