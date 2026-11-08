# The netfilter project's minimal netlink helper, here because iproute2 wants it.
#
# 1.0.5 is from 2019, which the "is it maintained or just old?" question in CLAUDE.md
# says to be suspicious of. It survives the question: libmnl is a ~30 KB wrapper around
# the netlink socket layout, it does exactly one thing, and it has been finished for
# years — Debian, Fedora and Alpine all ship this same release, and every netfilter tool
# in the archive links it. An old version number here is completion, not neglect.
VERSION="1.0.5"
PACKAGE="libmnl-${VERSION}"
TARBALL="$PACKAGE.tar.bz2"
URL="https://www.netfilter.org/pub/libmnl/${TARBALL}"
SIG_URL="${URL}.sig"
SHA256="274b9b919ef3152bfb3da3a13c950dd60d6e2bcd54230ffeca298d03b40d0525"
LICENSE="LGPL-2.1-or-later"
CPE="cpe:2.3:a:netfilter:libmnl:${VERSION}:*:*:*:*:*:*:*"
