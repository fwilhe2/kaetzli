# shadow-utils, for two of its programs: newuidmap and newgidmap, which write a process's
# user namespace ID maps from the ranges in /etc/subuid and /etc/subgid. That is the one
# privileged step rootless containers need — an unprivileged process may only map its
# own ID — and every rootless engine calls these two to take it. Nothing else of shadow
# ships; accounts here are /etc/passwd and systemd-sysusers.
VERSION="4.20.3"
PACKAGE="shadow-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://github.com/shadow-maint/shadow/releases/download/${VERSION}/${TARBALL}"
SIG_URL="${URL}.asc"
SHA256="623be9429f64fa2165bfc5957c6a642ec309c5cf23c0d53855c99f7591922297"
LICENSE="BSD-3-Clause"
# Two NVD products for one upstream: most advisories are shadow_project's, the newest
# shadow-maint's. Not debian:shadow, which is Debian's packaging.
CPE="cpe:2.3:a:shadow_project:shadow:${VERSION}:*:*:*:*:*:*:* cpe:2.3:a:shadow-maint:shadow-utils:${VERSION}:*:*:*:*:*:*:*"
# In the podman system extension, not the image (EXTENSION, see build.sh): the base keeps
# the container runtime — the kernel, crun, conmon — and an engine is loaded on demand
# (docs/guides/sysext.md, #192).
EXTENSION=podman
UPSTREAM_GITHUB="shadow-maint/shadow"
# Tags from before the 4.x numbering are dates (20001016); they are not releases.
UPSTREAM_IGNORE='^[0-9]{8}$'
