VERSION="6.6"
PACKAGE="ncurses-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://ftp.fau.de/gnu/ncurses/${TARBALL}"
MIRRORS="https://ftp.gnu.org/gnu/ncurses/${TARBALL}"
SHA256="355b4cbbed880b0381a04c46617b7656e362585d52e9cf84a67e2009b749ff11"
LICENSE="X11"
CPE="cpe:2.3:a:gnu:ncurses:${VERSION}:*:*:*:*:*:*:*"
# ncurses is released rarely and patched weekly: 6.6 plus ncurses-6.6-YYYYMMDD is the
# current upstream (Debian ships 6.6+20260608), and some of those patches are security
# fixes (CVE-2025-69720, in 20251231). Each is signed by Thomas E. Dickey's key, pinned in
# upstream-key.asc (19882D92DDA4C400C22C0D56CC2AF4472167BE03, the same from his site and
# keyserver.ubuntu.com); tools/sync-patches.sh refuses one that does not verify.
# The listing is upstream's own mirror: invisible-island.net is behind a Sucuri firewall
# that answers GitHub's runners with 403 on most nights; invisible-mirror.net has the same
# files and signatures without it.
UPSTREAM_PATCHES="https://invisible-mirror.net/archives/ncurses/${VERSION}/"
UPSTREAM_PATCH_REGEX="ncurses-${VERSION//./\\.}-[0-9]{8}\.patch\.gz"
UPSTREAM_PATCH_SIG=".asc"
# The release is patch 20251230 (VERSION in the tarball says so); the series is after it.
UPSTREAM_PATCH_SKIP="ncurses-${VERSION//./\\.}-20251230\.patch\.gz"
