# The image's TLS. Nothing here could open an HTTPS connection before it.
#
# mbedTLS would be smaller — ~1.5 MB against OpenSSL's ~5 — but the choice is settled the
# other way, on the "modern *and*
# established" test in CLAUDE.md. mbedTLS is established in embedded firmware; OpenSSL is
# what a general-purpose Linux has, and this image is meant to be one: Debian, Fedora and
# Alpine all ship it as *the* TLS library, and the next thing here that wants TLS —
# openssh is the obvious candidate — will look for libcrypto and find it. Carrying both
# is the outcome nobody wants, so the one to carry is the one that scales. It is modern
# too: 3.5 does TLS 1.3 and ML-KEM key exchange, and its Configure being perl was never
# a disqualification: constraint 5 is about what ships, not what builds.
#
# Two reasons this pins the 3.5 LTS branch rather than tracking whatever is newest, and
# only the second is negotiable:
#
#   - The SONAME has to agree with the builder image's headers. curl is compiled against
#     Debian sid's libssl-dev (3.6.x today), so its NEEDED says libcrypto.so.3 and
#     libssl.so.3, and it is our library that answers at runtime. OpenSSL 4.x is
#     libcrypto.so.4 — taking it before sid does would fail test/check-rootfs-deps.py,
#     and taking it silently is what an unbounded version check would do.
#   - 3.5 is supported to 2030; a non-LTS branch gets about a year. For the one library
#     here whose bugs are remotely exploitable, that is worth staying on.
#
# Being *behind* sid is safe in a way being ahead of it is not: the version nodes a
# binary asks for are the ones its library defined at link time, and curl uses nothing
# newer than OPENSSL_3.0.0. test/check-symbol-versions.py is what would say otherwise.
VERSION="3.5.9"
PACKAGE="openssl-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/openssl/openssl/releases/download/${PACKAGE}/${TARBALL}"
SIG_URL="${URL}.asc"
SHA256="603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a"
LICENSE="Apache-2.0"
CPE="cpe:2.3:a:openssl:openssl:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="openssl/openssl"
# Tags are openssl-3.5.7; the ancient ones are OpenSSL_1_1_1w and never survive the
# numeric filter in tools/upstream.py.
UPSTREAM_SED='s/^openssl-//'
# Everything whose first two components are not 3.5 — later minors, later majors, and
# the older branches that are still getting releases. See the two reasons above; this is
# the line to edit when Debian sid moves to a new SONAME.
UPSTREAM_IGNORE='(3\.[0-46-9]|3\.[0-9]{2}|[0-24-9]|[0-9]{2})\..*'
