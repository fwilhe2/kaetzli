# EDID and DisplayID parsing, which wlroots' DRM backend requires to name and size an
# output. A virtual screen has an EDID too.
VERSION="0.4.0"
PACKAGE="libdisplay-info-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://gitlab.freedesktop.org/emersion/libdisplay-info/-/releases/${VERSION}/downloads/${TARBALL}"
SHA256="43b180baa143e2035654759d84e2b2f5ee77d5fe817c423838c7fe59c0d68459"
LICENSE="MIT"
UPSTREAM_INDEX="https://gitlab.freedesktop.org/emersion/libdisplay-info/-/tags?format=atom"
UPSTREAM_REGEX='<title>([0-9]+\.[0-9]+\.[0-9]+)</title>'
EXTENSION=kiosk
DEPENDS="hwdata"
