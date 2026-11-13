# The reference CNI plugins, of which a node needs four: bridge and host-local for a
# single node's pods, loopback for every pod's lo, portmap for hostPort. A cluster's own
# CNI (flannel, Calico) installs its plugin next to these at runtime and delegates to
# them. Static Go. In the kubernetes system extension, not the image (EXTENSION, see
# build.sh): netavark, not CNI, is podman's network.
VERSION="1.10.0"
PACKAGE="cni-plugins-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/containernetworking/plugins/archive/refs/tags/v${VERSION}.tar.gz"
SHA256="15a36f0a88e90704126d2d523a9704c4cab2439a4a19c9ac23961b31c2a311c6"
LICENSE="Apache-2.0"
CPE="cpe:2.3:a:linuxfoundation:cni_network_plugins:${VERSION}:*:*:*:*:*:*:*"
EXTENSION=kubernetes
# No vendor/ since 1.10.0 (upstream #1320, at Debian's request): the modules its go.sum
# names are fetched with the tarball and vendored at extraction (tools/fetch-sources.sh).
GO_MODULES=1
UPSTREAM_GITHUB="containernetworking/plugins"
UPSTREAM_SED='s/^v//'
