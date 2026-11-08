VERSION="5.3"
PACKAGE="bash-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://ftp.fau.de/gnu/bash/${TARBALL}"
SIG_URL="${URL}.sig"
MIRRORS="https://ftp.gnu.org/gnu/bash/${TARBALL}"
SHA256="0d5cd86965f869a26cf64f4b71be7b96f90a3ba8b3d74e27e8e9d9d5550f31ba"
LICENSE="GPL-3.0-or-later"
# bash's fixes between releases are numbered patches, not tarballs: bash-5.3 plus
# bash53-001…NNN is the current upstream, and the release alone is not. They are in
# patches/upstream/, kept in step by tools/sync-patches.sh (docs/guides/patches.md), which
# verifies each against Chet Ramey's key, pinned in upstream-key.asc
# (7C0135FB088AAF6C66C650B9BB5869F064EA74AB, the same in GNU's keyring and on
# keyserver.ubuntu.com).
UPSTREAM_PATCHES="https://ftp.fau.de/gnu/bash/${PACKAGE}-patches/"
UPSTREAM_PATCH_REGEX="bash${VERSION//./}-[0-9]{3}"
UPSTREAM_PATCH_STRIP=0
CPE="cpe:2.3:a:gnu:bash:${VERSION}:*:*:*:*:*:*:*"
