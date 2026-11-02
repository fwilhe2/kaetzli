# The version is the date IANA's registries were snapshotted, not a release number.
# tools/upstream.sh handles it without help: it keeps candidates matching ^[0-9]+(\.[0-9]+)*$,
# which a bare date satisfies, and sort -V orders them correctly.
VERSION="20261027"
PACKAGE="iana-etc-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/Mic92/iana-etc/releases/download/${VERSION}/${TARBALL}"
SHA256="48981d07ab61179305abb9aa47ddbc50c7f0005ab0a4e08afd203fffad698873"
LICENSE="LicenseRef-IANA"
UPSTREAM_GITHUB="Mic92/iana-etc"
