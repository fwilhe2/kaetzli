# The protocol XML beyond the core (xdg-shell, layer-shell's base, presentation time,
# ...), from which wlroots, cage and foot generate their bindings. Build time only: what
# it installs is XML and a .pc file, which the extension's assembly removes as link-time
# files (image/build-extension.sh).
VERSION="1.50"
PACKAGE="wayland-protocols-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://gitlab.freedesktop.org/wayland/wayland-protocols/-/releases/${VERSION}/downloads/${TARBALL}"
SHA256="4460c20cfcf68b994a5ee206ed15fc19ad312f16c96075186e83b0e3f4aa48a5"
LICENSE="MIT"
UPSTREAM_INDEX="https://gitlab.freedesktop.org/wayland/wayland-protocols/-/tags?format=atom"
UPSTREAM_REGEX='<title>([0-9]+\.[0-9]+)</title>'
EXTENSION=kiosk
DEPENDS="wayland"
