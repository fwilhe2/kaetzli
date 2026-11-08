VERSION="1.16.2"
PACKAGE="dbus-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://dbus.freedesktop.org/releases/dbus/${TARBALL}"
SIG_URL="${URL}.asc"
SHA256="0ba2a1a4b16afe7bceb2c07e9ce99a8c2c3508e5dec290dbb643384bd6beb7e2"
LICENSE="AFL-2.1 OR GPL-2.0-or-later"
CPE="cpe:2.3:a:freedesktop:dbus:${VERSION}:*:*:*:*:*:*:*"
# dbus numbers its development releases with an odd minor (1.15.x, 1.17.x) and files
# them in the same directory as the stable ones, so skip them.
UPSTREAM_IGNORE='[0-9]+\.[0-9]*[13579]\..*'
