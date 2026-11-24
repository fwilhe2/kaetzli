# The trust store: Mozilla's CA list, which is what "curl https://..." verifies against.
# A TLS library with no roots to chain to fails every connection, so this is not optional
# once openssl and curl are here.
#
# This is the third package whose source is data rather than code, after iana-etc and the
# kernel's firmware-free config — and the second whose upstream is a *distribution*
# rather than the project itself. Mozilla publishes the roots as certdata.txt, a PKCS#11
# object dump inside NSS, and turning that into PEM takes a parser. Debian's
# ca-certificates is that parser plus a snapshot of certdata.txt, versioned by the date
# it was taken, and it is what Buildroot, OpenWrt, Void and Gentoo all build their
# bundles from. Taking it from the same place means the roots here are the roots Debian
# ships, decided by the people who already do that work.
#
# The alternative — curl.se's prebuilt cacert.pem — is one file and no parser, but it is
# not an archive, so it would be the only package in the tree that tools/prep.sh could
# not unpack. Not worth a special case for a build step that is one `make`.
#
# The parser is python and uses python3-cryptography; both are builder-side only. See
# CLAUDE.md: what the image may not gain is an interpreter, and what ships from here is
# a text file.
VERSION="20261123"
PACKAGE="ca-certificates-${VERSION}"
TARBALL="ca-certificates_${VERSION}.tar.xz"
URL="https://deb.debian.org/debian/pool/main/c/ca-certificates/${TARBALL}"
SHA256="ca7c16b757f342d34248874edf40e15a85d6dcf221b7e2ef2fe026e4fa4c6a51"
# certdata.txt is Mozilla's, under MPL-2.0; the Makefiles and certdata2pem.py are
# Debian's, under GPL-2.0-or-later. Only the first half reaches the image — what ships is
# the certificates.
LICENSE="MPL-2.0 AND GPL-2.0-or-later"
# A Debian native package, so the tarball is in the pool directory the URL points into
# and the default index scrape finds it. A sid upload replaces the previous one there, so
# a pinned version disappears from the pool the day it is superseded;
# tools/fetch-sources.sh then falls back to snapshot.debian.org.
#
# Non-maintainer uploads (20260816+nmu1) are proposed as updates: they are sid uploads
# like any other, and for this package they are usually a distrust that cannot wait for
# the next Mozilla bundle. Point releases are spelled 20230311+deb12u1 and still never
# match, which is right: those are stable backports, not new snapshots.
UPSTREAM_NUM='[0-9]{8}(\+nmu[0-9]+)?'
