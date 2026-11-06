# Font discovery and matching: "monospace" to a file. Its configuration is /etc/fonts,
# which the image's /etc does not have, so it ships in the kiosk confext (staged etc/,
# image/build-extension.sh).
VERSION="2.18.3"
PACKAGE="fontconfig-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://gitlab.freedesktop.org/api/v4/projects/890/packages/generic/fontconfig/${VERSION}/${TARBALL}"
SHA256="4f7b554a38cdf78c033f666c8871f3749e14a094f65a07f630c91ed0b43d35e3"
LICENSE="HPND-sell-variant AND MIT-Modern-Variant"
CPE="cpe:2.3:a:fontconfig_project:fontconfig:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_INDEX="https://gitlab.freedesktop.org/fontconfig/fontconfig/-/tags?format=atom"
UPSTREAM_REGEX='<title>([0-9]+\.[0-9]+\.[0-9]+)</title>'
# An odd minor is a development series (2.17.x).
UPSTREAM_IGNORE='[0-9]+\.[0-9]*[13579]\..*'
EXTENSION=kiosk
DEPENDS="freetype"
