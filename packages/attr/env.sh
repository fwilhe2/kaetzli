VERSION="2.6.0"
PACKAGE="attr-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://download.savannah.nongnu.org/releases/${PKG}/${TARBALL}"
SIG_URL="${URL}.sig"
# download.savannah.nongnu.org serves a 502 often enough to have failed a CI run on its
# own; the mirror pool behind this name is the same content and has not.
MIRRORS="https://download-mirror.savannah.gnu.org/releases/${PKG}/${TARBALL}"
SHA256="d42fa374513180bb48cb11a46696f488240e5124ff1e6ad88b0abff706985612"
LICENSE="LGPL-2.1-or-later AND GPL-2.0-or-later"
CPE="cpe:2.3:a:attr_project:attr:${VERSION}:*:*:*:*:*:*:*"
