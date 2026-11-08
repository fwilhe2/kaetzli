VERSION="5.4.1"
PACKAGE="gawk-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://ftp.fau.de/gnu/gawk/${TARBALL}"
SIG_URL="${URL}.sig"
MIRRORS="https://ftp.gnu.org/gnu/gawk/${TARBALL}"
SHA256="8b3b0ea83930311a3f30905d3ce898d32c6103c2fe20d6a90b40341171b174de"
LICENSE="GPL-3.0-or-later"
# NVD files gawk's advisories under two vendors: GNU for the older, and Fossies (a
# source mirror) for those since 2026. Both, or half of them go unmatched.
CPE="cpe:2.3:a:gnu:gawk:${VERSION}:*:*:*:*:*:*:* cpe:2.3:a:fossies:gawk:${VERSION}:*:*:*:*:*:*:*"
