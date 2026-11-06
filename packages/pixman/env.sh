# Pixel compositing in software: wlroots' pixman renderer (the kiosk has no GPU driver
# in userspace, no mesa) and fcft's glyph rendering.
VERSION="0.46.4"
PACKAGE="pixman-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://www.x.org/releases/individual/lib/${TARBALL}"
SHA256="a098c33924754ad43f981b740f6d576c70f9ed1006e12221b1845431ebce1239"
LICENSE="MIT"
CPE="cpe:2.3:a:pixman:pixman:${VERSION}:*:*:*:*:*:*:*"
# An odd minor is a development series.
UPSTREAM_IGNORE='[0-9]+\.[0-9]*[13579]\..*'
EXTENSION=kiosk
