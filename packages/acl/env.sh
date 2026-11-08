VERSION="2.4.0"
PACKAGE="acl-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://download.savannah.nongnu.org/releases/${PKG}/${TARBALL}"
SIG_URL="${URL}.sig"
# download.savannah.nongnu.org serves a 502 often enough to have failed a CI run on its
# own; the mirror pool behind this name is the same content and has not.
MIRRORS="https://download-mirror.savannah.gnu.org/releases/${PKG}/${TARBALL}"
SHA256="73c853c3d44e1f693e5a96a986f1bd19d3d0dac2c7d453e796177774bc4e5f6a"
LICENSE="LGPL-2.1-or-later AND GPL-2.0-or-later"
CPE="cpe:2.3:a:acl_project:acl:${VERSION}:*:*:*:*:*:*:*"
