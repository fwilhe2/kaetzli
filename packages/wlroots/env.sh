# The compositor library cage is built on: DRM/KMS output, libinput input, the Wayland
# protocols. Software rendering only (its pixman renderer into DRM dumb buffers): no GL
# or Vulkan, so no mesa and no LLVM (docs/proposals/kiosk.md, *The stack*).
VERSION="0.20.2"
PACKAGE="wlroots-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://gitlab.freedesktop.org/wlroots/wlroots/-/releases/${VERSION}/downloads/${TARBALL}"
SHA256="80c567d2ed4efb2cfa6b077f22c7a710d9c78ba4e185a75e8694001114af0733"
LICENSE="MIT"
UPSTREAM_INDEX="https://gitlab.freedesktop.org/wlroots/wlroots/-/tags?format=atom"
UPSTREAM_REGEX='<title>([0-9]+\.[0-9]+\.[0-9]+)</title>'
EXTENSION=kiosk
DEPENDS="wayland wayland-protocols libdrm pixman libxkbcommon libinput seatd libdisplay-info hwdata systemd"
