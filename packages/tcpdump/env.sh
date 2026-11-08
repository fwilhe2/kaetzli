# tcpdump: packets on an interface, as they are. What a container's traffic looks like
# on its veth, on podman's bridge and on the uplink is the one question about netavark,
# pasta or a CNI that their own logs never answer.
#
# libpcap is a second tarball rather than a package of its own, and linked statically.
# An extension's packages compile against the base's staging tree, never against each
# other's (build.sh), so a libpcap package would have to put libpcap in the base for
# every machine to carry — and tcpdump is its only consumer. The tcpdump project
# releases both, from the same site, and documents building them side by side.
VERSION="4.99.7"
PACKAGE="tcpdump-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://www.tcpdump.org/release/${TARBALL}"
SIG_URL="${URL}.sig"
SHA256="39675dfa06fb0e99be8da5d4a1784acbaab58ae0870231d956ff7870cb99eec1"
# Its own version, not tcpdump's, so VENDOR_VERSION: tools/check-updates.py watches it
# as tcpdump/libpcap, and `tools/bump-version.sh --vendor tcpdump <v>` moves it.
VENDOR_NAME=libpcap
VENDOR_VERSION="1.11.0"
VENDOR_TARBALL="libpcap-${VENDOR_VERSION}.tar.xz"
VENDOR_URL="https://www.tcpdump.org/release/${VENDOR_TARBALL}"
VENDOR_SHA256="9237f5bae9dcf3a91823d9963ec43b7c0e2e3374ef2ad57d92c8cd39530f4723"
LICENSE="BSD-3-Clause"
CPE="cpe:2.3:a:tcpdump:tcpdump:${VERSION}:*:*:*:*:*:*:*"
# In the debug system extension (docs/guides/debugging.md).
EXTENSION=debug
