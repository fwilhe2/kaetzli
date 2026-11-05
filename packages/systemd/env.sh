VERSION="262.2"
PACKAGE="systemd-${VERSION}"
TARBALL="v${VERSION}.tar.gz"
URL="https://github.com/systemd/systemd/archive/refs/tags/${TARBALL}"
SHA256="116432ee9dce9bfef4c7da7963c6de41899ab44197f2876ee99545707a5c7c1f"
LICENSE="LGPL-2.1-or-later AND GPL-2.0-or-later"
CPE="cpe:2.3:a:systemd_project:systemd:${VERSION}:*:*:*:*:*:*:*"
# build-dep doesn't pull libcap-dev, which systemd's meson requires (sys/capability.h).
UPSTREAM_GITHUB="systemd/systemd"
