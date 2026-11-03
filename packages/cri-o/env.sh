# The container runtime kubelet talks to: CRI-O, the Kubernetes CRI from the same
# ecosystem as podman, sharing its image and storage libraries, policy.json,
# registries.conf, conmon and crun — all of which the image already ships. Go with cgo
# for libseccomp, plus pinns in C; dependencies vendored in the repository.
# In the kubernetes system extension, not the image (EXTENSION, see build.sh).
#
# CRI-O's minor tracks Kubernetes': 1.37 serves kubelet 1.37, and packages/kubernetes
# has to be bumped with it.
VERSION="1.37.3"
PACKAGE="cri-o-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/cri-o/cri-o/archive/refs/tags/v${VERSION}.tar.gz"
SHA256="4cf343f8080772be4a51d5ccf397d76690b7ad4c0988d92e1588807ad59bfbb7"
LICENSE="Apache-2.0"
CPE="cpe:2.3:a:kubernetes:cri-o:${VERSION}:*:*:*:*:*:*:*"
EXTENSION=kubernetes
UPSTREAM_GITHUB="cri-o/cri-o"
UPSTREAM_SED='s/^v//'
