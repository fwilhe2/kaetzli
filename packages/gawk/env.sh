VERSION="5.4.2"
PACKAGE="gawk-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://ftp.fau.de/gnu/gawk/${TARBALL}"
SIG_URL="${URL}.sig"
MIRRORS="https://ftp.gnu.org/gnu/gawk/${TARBALL}"
SHA256="79c46f5de14401663c63c8df4fc45c3bda76f6329d73b213523a7c71f32f7243"
LICENSE="GPL-3.0-or-later"
# NVD files gawk's advisories under two vendors: GNU for the older, and Fossies (a
# source mirror) for those since 2026. Both, or half of them go unmatched.
CPE="cpe:2.3:a:gnu:gawk:${VERSION}:*:*:*:*:*:*:* cpe:2.3:a:fossies:gawk:${VERSION}:*:*:*:*:*:*:*"
