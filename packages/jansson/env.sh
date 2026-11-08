# A C library for JSON, here for nftables: `nft -j` — the JSON interface netavark drives
# the firewall through — only exists in an nft built --with-json, and that is jansson.
# Small, finished in the good sense, and what every distribution builds nftables against.
VERSION="2.15.1"
PACKAGE="jansson-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/akheron/jansson/releases/download/v${VERSION}/${TARBALL}"
SIG_URL="${URL}.asc"
SHA256="0c7114dc0b2d22a670724a1f95922029d7077c19dbf79a584cb8084d2f267f2f"
LICENSE="MIT"
CPE="cpe:2.3:a:jansson_project:jansson:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="akheron/jansson"
