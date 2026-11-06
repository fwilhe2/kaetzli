# Input devices for the compositor: keyboards, pointers and tablets through udev and
# evdev, with the acceleration and gesture handling every Wayland compositor shares.
VERSION="1.32.0"
PACKAGE="libinput-${VERSION}"
TARBALL="$PACKAGE.tar.bz2"
URL="https://gitlab.freedesktop.org/libinput/libinput/-/archive/${VERSION}/${TARBALL}"
SHA256="3a6065c07b118c08d0c7b7b85a265ecb050a8cfc91eeb360890d768a51b4cce9"
LICENSE="MIT"
CPE="cpe:2.3:a:freedesktop:libinput:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_INDEX="https://gitlab.freedesktop.org/libinput/libinput/-/tags?format=atom"
UPSTREAM_REGEX='<title>([0-9]+\.[0-9]+\.[0-9]+)</title>'
# x.y.901 and up are release candidates.
UPSTREAM_IGNORE='[0-9]+\.[0-9]+\.9[0-9]+'
EXTENSION=kiosk
# libudev is the base's, from systemd.
DEPENDS="libevdev mtdev systemd"
