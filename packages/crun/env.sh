VERSION="1.30.1"
PACKAGE="crun-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
# The release asset, not the git archive: it ships a generated ./configure and the ~78
# pre-generated libocispec parser sources, so nothing has to run autogen.sh.
URL="https://github.com/containers/crun/releases/download/${VERSION}/${TARBALL}"
SIG_URL="${URL}.asc"
SHA256="99d036d95113552b6df66c7c879432835504bf0e525fc0372745071df129527b"
LICENSE="GPL-2.0-or-later AND LGPL-2.1-or-later"
CPE="cpe:2.3:a:crun_project:crun:${VERSION}:*:*:*:*:*:*:*"
# Debian's crun build-deps install libseccomp-dev, libcriu-dev and libprotobuf-c-dev,
# and crun's configure autodetects every one of them — which is the "linked against a
# library only the builder image has" trap. List what this build actually links.
UPSTREAM_GITHUB="containers/crun"
