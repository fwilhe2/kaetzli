VERSION="1.31"
PACKAGE="crun-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
# The release asset, not the git archive: it ships a generated ./configure and the ~78
# pre-generated libocispec parser sources, so nothing has to run autogen.sh.
URL="https://github.com/containers/crun/releases/download/${VERSION}/${TARBALL}"
SIG_URL="${URL}.asc"
SHA256="17d04ba159fada1f1300a36b8afe869afb38bf1bbda28da052e83bb4bee20108"
LICENSE="GPL-2.0-or-later AND LGPL-2.1-or-later"
CPE="cpe:2.3:a:crun_project:crun:${VERSION}:*:*:*:*:*:*:*"
# Debian's crun build-deps install libseccomp-dev, libcriu-dev and libprotobuf-c-dev,
# and crun's configure autodetects every one of them — which is the "linked against a
# library only the builder image has" trap. List what this build actually links.
UPSTREAM_GITHUB="containers/crun"
