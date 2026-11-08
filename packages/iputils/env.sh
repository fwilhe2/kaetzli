# ping, and the two other things worth having next to it. The first command anyone types
# on a machine whose network looks wrong.
#
# iputils is the ping every Linux distribution ships — Debian, Fedora, Arch and Alpine's
# non-busybox build all take it from here. It moved to meson and dropped its museum
# (rarpd, rdisc, traceroute6, tftpd) over the last few releases, so what is left is four
# small C programs rather than a 1990s network suite.
#
# Releases are dated rather than numbered, which tools/upstream.py handles unaided: it
# keeps candidates matching ^[0-9]+(\.[0-9]+)*$, and a bare date satisfies that. The
# tarball is a release asset, not GitHub's generated archive — those are regenerated on
# demand and have changed bytes under a fixed tag before now, which a SHA256 pin turns
# into a build that stops.
VERSION="20250605"
PACKAGE="iputils-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://github.com/iputils/iputils/releases/download/${VERSION}/${TARBALL}"
SIG_URL="${URL}.asc"
SHA256="6f213700dbf96b5cc4499ca70cb15ecd69c09f405b06785bb4a1a10b572b6276"
# ping and clockdiff descend from the 4.3BSD original; arping and tracepath are Alexey
# Kuznetsov's and are GPL. See LICENSE, which lists it per program.
LICENSE="BSD-3-Clause AND GPL-2.0-or-later"
CPE="cpe:2.3:a:iputils_project:iputils:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="iputils/iputils"
