# The cryptography behind WebKit's Web Crypto API, which WPE requires.
VERSION="1.12.4"
PACKAGE="libgcrypt-${VERSION}"
TARBALL="$PACKAGE.tar.bz2"
URL="https://gnupg.org/ftp/gcrypt/libgcrypt/${TARBALL}"
SHA256="d77f68f48879510e79a2f65977ccc68981781ea0923e5bdffac2a193ea3d660e"
LICENSE="LGPL-2.1-or-later"
CPE="cpe:2.3:a:gnupg:libgcrypt:${VERSION}:*:*:*:*:*:*:*"
EXTENSION=kiosk
DEPENDS="libgpg-error"
