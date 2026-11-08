# nft, the nftables command line, on the libmnl and libnftnl already here for iptables.
# netavark (the podman network backend) has no iptables driver since 2.0: it builds its
# rules as JSON and hands them to `nft -j`, so this is what container networking runs on.
# It is also simply the firewall tool for this kernel, which has no legacy xtables.
VERSION="1.1.7"
PACKAGE="nftables-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://www.netfilter.org/pub/nftables/${TARBALL}"
SIG_URL="${URL}.sig"
SHA256="a6fbf060d8d4fff001517a2b94f356bb4366bfbf0ba366366f9d27cc38caa58f"
LICENSE="GPL-2.0-only"
