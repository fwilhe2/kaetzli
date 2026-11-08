VERSION="4.11.0"
PACKAGE="findutils-${VERSION}"
# .tar.xz, unlike the other GNU packages here: findutils has not shipped a .tar.gz
# since 4.6.0, so there is no consistent-with-coreutils option to pick.
TARBALL="$PACKAGE.tar.xz"
URL="https://ftp.fau.de/gnu/findutils/${TARBALL}"
SIG_URL="${URL}.sig"
MIRRORS="https://ftp.gnu.org/gnu/findutils/${TARBALL}"
SHA256="bfd19cb06cc71f3352d567e90284d8cdac02ac89774bbeadf0b533b0c11432fd"
LICENSE="GPL-3.0-or-later"
CPE="cpe:2.3:a:gnu:findutils:${VERSION}:*:*:*:*:*:*:*"
