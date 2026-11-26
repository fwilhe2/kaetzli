# The node agent and its tools: kubelet, kubeadm and kubectl. Go, with every module
# vendored in the repository, so the tag's archive builds with --network=none as it is.
# Not part of the image: EXTENSION puts it in the kubernetes system extension
# (docs/guides/sysext.md), which is where software goes that only a Kubernetes node wants.
VERSION="1.37.3"
PACKAGE="kubernetes-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/kubernetes/kubernetes/archive/refs/tags/v${VERSION}.tar.gz"
SHA256="e855a5151a209537df57e10c13eb07ab164e2fc50137a6649e99e5366bc0bb57"
LICENSE="Apache-2.0"
CPE="cpe:2.3:a:kubernetes:kubernetes:${VERSION}:*:*:*:*:*:*:*"
EXTENSION=kubernetes
UPSTREAM_GITHUB="kubernetes/kubernetes"
UPSTREAM_SED='s/^v//'
