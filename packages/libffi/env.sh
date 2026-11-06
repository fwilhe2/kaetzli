# libffi: calls a C function whose signature is only known at run time. libwayland
# marshals every protocol message through it, so the kiosk extension's Wayland stack
# needs the shared library, which the base does not ship (glib's build only borrows the
# builder's libffi-dev, packages/glib/build.sh).
VERSION="3.8.0"
PACKAGE="libffi-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/libffi/libffi/releases/download/v${VERSION}/${TARBALL}"
SHA256="7da3e2d9a171eb0a038f592ecad3ff2bb2550f3496d87b3b29ad0cf4430c0db4"
LICENSE="MIT"
CPE="cpe:2.3:a:libffi_project:libffi:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="libffi/libffi"
EXTENSION=kiosk
