# A typed linked list in one C header, which fcft and foot use. Build time only: the
# header goes with the other link-time files at assembly.
VERSION="1.1.0"
PACKAGE="tllist-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://codeberg.org/dnkl/tllist/archive/${VERSION}.tar.gz"
SHA256="0e7b7094a02550dd80b7243bcffc3671550b0f1d8ba625e4dff52517827d5d23"
LICENSE="MIT"
UPSTREAM_INDEX="https://codeberg.org/dnkl/tllist/tags.rss"
UPSTREAM_REGEX='<title>([0-9]+\.[0-9]+\.[0-9]+)</title>'
EXTENSION=kiosk
