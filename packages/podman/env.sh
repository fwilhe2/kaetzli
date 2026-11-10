# The container engine: pull, store, run and network OCI containers, daemonless, with a
# Docker-compatible CLI and API, and Quadlet for declaring containers as systemd units.
# Go; its dependencies are vendored in the repository, so the tag's archive builds with
# --network=none as it is. Runs containers with crun, networks them with netavark.
VERSION="6.2.0"
PACKAGE="podman-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/containers/podman/archive/refs/tags/v${VERSION}.tar.gz"
SHA256="cb182273c5c5e3ea079f8f88a9a30a574d247f80e0b9dd9c02df1d2e6f6f3705"
LICENSE="Apache-2.0"
CPE="cpe:2.3:a:podman_project:podman:${VERSION}:*:*:*:*:*:*:*"
# In the podman system extension, not the image (EXTENSION, see build.sh): the base keeps
# the container runtime — the kernel, crun, conmon — and an engine is loaded on demand
# (docs/guides/sysext.md, #192).
EXTENSION=podman
UPSTREAM_GITHUB="containers/podman"
UPSTREAM_SED='s/^v//'
