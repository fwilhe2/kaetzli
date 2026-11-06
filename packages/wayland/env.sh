# The Wayland protocol libraries: libwayland-server for the compositor (wlroots, cage),
# libwayland-client for every application. Also wayland-scanner, which turns protocol
# XML into C at build time for everything above it (found through wayland-scanner.pc in
# the dependency sysroot, build.sh).
VERSION="1.26.0"
PACKAGE="wayland-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://gitlab.freedesktop.org/wayland/wayland/-/releases/${VERSION}/downloads/${TARBALL}"
SHA256="64176eaa46e4969903e286f8e5ef8331affc17fdf03ac9b58381d2b23162b7a3"
LICENSE="MIT"
CPE="cpe:2.3:a:wayland:wayland:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_INDEX="https://gitlab.freedesktop.org/wayland/wayland/-/tags?format=atom"
UPSTREAM_REGEX='<title>([0-9]+\.[0-9]+\.[0-9]+)</title>'
# x.y.90 and up are the release candidates for the next minor.
UPSTREAM_IGNORE='[0-9]+\.[0-9]+\.9[0-9]+'
EXTENSION=kiosk
DEPENDS="libffi"
