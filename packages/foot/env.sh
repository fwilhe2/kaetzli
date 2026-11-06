# A Wayland terminal: the client the kiosk's compositor runs until there is a browser,
# and later the admin terminal (docs/proposals/kiosk.md, *Admin mode*). Small, fast,
# software-rendered, in Debian, Fedora and Alpine.
VERSION="1.28.0"
PACKAGE="foot-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://codeberg.org/dnkl/foot/archive/${VERSION}.tar.gz"
SHA256="4296be402b5684d049534598e69db92b918f92beac9dab76b585207045f0b037"
LICENSE="MIT"
UPSTREAM_INDEX="https://codeberg.org/dnkl/foot/tags.rss"
UPSTREAM_REGEX='<title>([0-9]+\.[0-9]+\.[0-9]+)</title>'
EXTENSION=kiosk
DEPENDS="wayland wayland-protocols libxkbcommon pixman fontconfig tllist fcft"
