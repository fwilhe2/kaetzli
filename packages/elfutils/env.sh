VERSION="0.197"
PACKAGE="elfutils-${VERSION}"
TARBALL="$PACKAGE.tar.bz2"
URL="https://sourceware.org/elfutils/ftp/${VERSION}/${TARBALL}"
SIG_URL="${URL}.sig"
SHA256="1fdf525f22dcf796d99a99ba3ea00a1f7b1ae5421bc62e8443a708920b99b904"
LICENSE="GPL-3.0-or-later AND (LGPL-3.0-or-later OR GPL-2.0-or-later)"
CPE="cpe:2.3:a:elfutils_project:elfutils:${VERSION}:*:*:*:*:*:*:*"
# Here only for libelf.so.1, which libbpf links against.
# The FTP directory holds one subdirectory per release rather than the tarballs
# side by side, so look a level up.
UPSTREAM_INDEX="https://sourceware.org/elfutils/ftp/"
UPSTREAM_SUBDIR="[0-9]+\.[0-9]+/"
