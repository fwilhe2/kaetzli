# SQLite: the browser's storage (IndexedDB, local storage, cookies, the HSTS cache).
VERSION="3.54.1"
_v=${VERSION//./ }; read -r _ma _mi _pa <<< "$_v"
PACKAGE="sqlite-autoconf-$(printf '%d%02d%02d00' "$_ma" "$_mi" "$_pa")"
unset _v _ma _mi _pa
TARBALL="$PACKAGE.tar.gz"
URL="https://sqlite.org/2026/${TARBALL}"
SHA256="926ef8d3eab93fbf6e06011901273eec076b25594a4e1cdadd50c987f4f6d64e"
LICENSE="blessing"
CPE="cpe:2.3:a:sqlite:sqlite:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_INDEX="https://sqlite.org/index.html"
UPSTREAM_REGEX='Version ([0-9]+\.[0-9]+\.[0-9]+)'
EXTENSION=kiosk
