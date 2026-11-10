VERSION="7.3.4"
PACKAGE="linux-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://cdn.kernel.org/pub/linux/kernel/v${VERSION%%.*}.x/${TARBALL}"
SHA256="b11b6df8295e8e991ff040b71aaa4994a8b55caa23b6b092907f195b5840fd1f"
LICENSE="GPL-2.0-only WITH Linux-syscall-note"
CPE="cpe:2.3:o:linux:linux_kernel:${VERSION}:*:*:*:*:*:*:*"
# Debian's linux source package pulls in a whole distro kernel toolchain (and its
# build-dep list breaks whenever sid moves), so list what this build actually needs.
# The kernel is freestanding — it links against no libc at all, and kbuild builds its
# host tools with the builder's own headers — so there is nothing to point at a sysroot.
NO_SYSROOT=1
# Each major series has its own directory, and $URL only ever points into the one the
# pinned version belongs to — so look a level up, or 7.x would stay invisible forever
# to a pin in 6.x. Release candidates are filed separately under v7.x/testing/.
UPSTREAM_INDEX="https://cdn.kernel.org/pub/linux/kernel/"
UPSTREAM_SUBDIR="v[0-9]+\.x/"
# Only point releases: x.y.0 is filed next to them but is what kernel.org still calls
# mainline, and the regressions a merge window brings in are what x.y.1 and x.y.2 fix.
# Following stable means waiting for the first of those, not pinning to a longterm
# series — 7.3 is skipped while 7.2.11 is current, and 7.3.1 is proposed when it exists.
UPSTREAM_IGNORE='[0-9]+\.[0-9]+'
