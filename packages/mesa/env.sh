# EGL and GBM, with one software driver: WebKit needs an EGL display even when it paints
# on the CPU, and has nowhere to put pixels without one. softpipe, not llvmpipe, so no
# LLVM; the browser paints pages with Skia on the CPU and only composites through GL.
VERSION="26.2.6"
PACKAGE="mesa-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://archive.mesa3d.org/${TARBALL}"
SHA256="b9436b3e1a8be76fbd4823dc884f90596ab7c6ac5e1867a305e66ffccfb31a92"
LICENSE="MIT"
CPE="cpe:2.3:a:mesa3d:mesa:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_INDEX="https://archive.mesa3d.org/"
UPSTREAM_REGEX='mesa-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.xz'
EXTENSION=kiosk
DEPENDS="libdrm wayland wayland-protocols"
