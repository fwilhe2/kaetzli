# crictl, the CRI's own command line: what `podman ps` is to podman, for the containers
# kubelet runs through containerd. Static Go, vendored in the repository. In the kubernetes
# system extension, not the image (EXTENSION, see build.sh). Versioned with Kubernetes.
VERSION="1.37.0"
PACKAGE="cri-tools-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/kubernetes-sigs/cri-tools/archive/refs/tags/v${VERSION}.tar.gz"
SHA256="ef81c240412a5b77b164ae6570857b7a0f347770031a697764ba5167389842d8"
LICENSE="Apache-2.0"
EXTENSION=kubernetes
UPSTREAM_GITHUB="kubernetes-sigs/cri-tools"
UPSTREAM_SED='s/^v//'
