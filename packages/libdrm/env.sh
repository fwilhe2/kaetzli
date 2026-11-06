# The userspace side of the kernel's DRM interface: modesetting for wlroots' DRM backend.
# Only the core library: every GPU vendor's helper library is for hardware a VM does not
# have (constraint 2), and virtio-gpu needs none.
VERSION="2.4.134"
PACKAGE="libdrm-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://dri.freedesktop.org/libdrm/${TARBALL}"
SHA256="ac5e74d157830eb8bee44c6a6bf3ad49774ef0dd2a72bdad74a8f20308b52a95"
LICENSE="MIT"
EXTENSION=kiosk
