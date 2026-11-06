# Multitouch protocol A to B translation. libinput requires it at build time, whatever
# devices the machine has; a VM's tablet speaks B already. Finished rather than abandoned:
# 1.1.7 (2023) is what Debian, Fedora and Alpine ship.
VERSION="1.1.7"
PACKAGE="mtdev-${VERSION}"
TARBALL="$PACKAGE.tar.bz2"
URL="https://bitmath.org/code/mtdev/${TARBALL}"
SHA256="a107adad2101fecac54ac7f9f0e0a0dd155d954193da55c2340c97f2ff1d814e"
LICENSE="MIT"
EXTENSION=kiosk
