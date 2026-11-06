# JPEG decoding for the browser.
VERSION="3.2.1"
PACKAGE="libjpeg-turbo-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/${VERSION}/${TARBALL}"
SHA256="c27ad72a68da151177929aa969ae773a2da5ac2af9ce946079314fdac8cece7f"
LICENSE="IJG AND BSD-3-Clause AND Zlib"
CPE="cpe:2.3:a:libjpeg-turbo:libjpeg-turbo:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="libjpeg-turbo/libjpeg-turbo"
# x.y.90 and up are betas.
UPSTREAM_IGNORE='[0-9]+\.[0-9]+\.9[0-9]+'
EXTENSION=kiosk
