# The PNP vendor IDs monitors report in their EDID, which libdisplay-info and wlroots
# compile into their name tables at build time. Data only, and only pnp.ids is kept; the
# PCI and USB databases serve lspci and lsusb, which nothing ships.
VERSION="0.413"
PACKAGE="hwdata-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/vcrhonek/hwdata/archive/refs/tags/v${VERSION}.tar.gz"
SHA256="5a71ab7a48c668ef912fcfdbec046949087019f8dd7fb967445d36fbeb778350"
LICENSE="GPL-2.0-or-later OR XFree86-1.0"
UPSTREAM_GITHUB="vcrhonek/hwdata"
EXTENSION=kiosk
