# The container runtime kubelet talks to: containerd, with ctr and the runc v2 shim,
# pointed at the base's crun rather than runc (build.sh). The CRI that kubeadm, Gardener
# and most managed Kubernetes default to; Gardener's node init also pulls its agent with
# ctr. Go, its dependencies vendored in the repository. In the kubernetes system
# extension, not the image (docs/guides/sysext.md).
VERSION="2.4.2"
PACKAGE="containerd-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/containerd/containerd/archive/refs/tags/v${VERSION}.tar.gz"
SHA256="02cbbecd0ef78a521d124afa791b843ba5458ef206e1f7f017999ff45c80c0b4"
LICENSE="Apache-2.0"
CPE="cpe:2.3:a:linuxfoundation:containerd:${VERSION}:*:*:*:*:*:*:*"
EXTENSION=kubernetes
UPSTREAM_GITHUB="containerd/containerd"
UPSTREAM_SED='s/^v//'
