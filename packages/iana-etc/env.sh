# The version is the date IANA's registries were snapshotted, not a release number.
# tools/upstream.py handles it without help: it keeps candidates matching ^[0-9]+(\.[0-9]+)*$,
# which a bare date satisfies, and sort -V orders them correctly.
VERSION="20261106"
PACKAGE="iana-etc-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/Mic92/iana-etc/releases/download/${VERSION}/${TARBALL}"
SHA256="933a46404c521996e94d2374af5d9dfface321c522b6ee710f180326d45a60df"
LICENSE="LicenseRef-IANA"
UPSTREAM_GITHUB="Mic92/iana-etc"
