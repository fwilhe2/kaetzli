# The seccomp filter library: compiles a syscall policy into the BPF program the kernel
# runs on every syscall. Here so that a container's seccomp profile is enforced rather
# than accepted and ignored — crun and podman both hand theirs to this — and so that
# systemd's SystemCallFilter= and friends work, which -Dseccomp=disabled had switched off
# for every unit that asks.
#
# Maintained (2.6.x, 2026), and the one implementation: every distribution ships it, and
# Docker's and podman's default profiles are written against it.
VERSION="2.6.1"
PACKAGE="libseccomp-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/seccomp/libseccomp/releases/download/v${VERSION}/${TARBALL}"
SIG_URL="${URL}.asc"
SHA256="501f66c667225d53791b97e1d7cf85ab764c297d04881f60f38f451c4b0ee1be"
LICENSE="LGPL-2.1-only"
CPE="cpe:2.3:a:libseccomp_project:libseccomp:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="seccomp/libseccomp"
