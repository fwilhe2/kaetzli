# libseat: how a compositor gets the screen and input devices without being root. Here
# it asks logind (the base's), so no seatd daemon runs; logind hands the session's
# devices over and takes them back on a VT switch.
VERSION="0.9.3"
PACKAGE="seatd-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://git.sr.ht/~kennylevinsen/seatd/archive/${VERSION}.tar.gz"
SHA256="302564d54d8e28191fadfd734f2675ecb0c9e0615a58011b89ef15dfa4dbaa96"
LICENSE="MIT"
UPSTREAM_INDEX="https://git.sr.ht/~kennylevinsen/seatd/refs"
UPSTREAM_REGEX='archive/([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz'
EXTENSION=kiosk
DEPENDS="systemd"
