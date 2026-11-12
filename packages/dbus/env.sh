VERSION="1.16.4"
PACKAGE="dbus-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://dbus.freedesktop.org/releases/dbus/${TARBALL}"
SIG_URL="${URL}.asc"
SHA256="d08b05526c2cb7966a5fb79210d6112b1aca3d135f315c1b302e14b4b0297dac"
LICENSE="AFL-2.1 OR GPL-2.0-or-later"
CPE="cpe:2.3:a:freedesktop:dbus:${VERSION}:*:*:*:*:*:*:*"
# dbus numbers its development releases with an odd minor (1.15.x, 1.17.x) and files
# them in the same directory as the stable ones, so skip them.
UPSTREAM_IGNORE='[0-9]+\.[0-9]*[13579]\..*'
