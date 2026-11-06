# The Public Suffix List, compiled in: which part of a host name is a registrable
# domain, which libsoup asks before letting a site set a cookie.
VERSION="0.23.3"
PACKAGE="libpsl-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/rockdaboot/libpsl/releases/download/${VERSION}/${TARBALL}"
SHA256="93941f85a1e7bd593fa94f299233cb5dfc91cd144fd9a78a6ceb75001c5b03be"
LICENSE="MIT"
CPE="cpe:2.3:a:libpsl_project:libpsl:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="rockdaboot/libpsl"
EXTENSION=kiosk
DEPENDS="icu"
