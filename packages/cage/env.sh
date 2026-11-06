# The compositor: one application, fullscreen, nothing else on screen, and the session
# ends when the application does. Built on wlroots for exactly the kiosk case. VT
# switching stays off unless started with -s.
VERSION="0.3.1"
PACKAGE="cage-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://github.com/cage-kiosk/cage/releases/download/v${VERSION}/${TARBALL}"
SHA256="edafc173b6f56a3e1564933e272a54aaa9fe158d330e6a415d57b44ab880fe2b"
LICENSE="MIT"
UPSTREAM_GITHUB="cage-kiosk/cage"
EXTENSION=kiosk
DEPENDS="wlroots"
# The session it runs (build.sh) is a kiosk.service in the sysext, with a userdb
# record for its user and a PAM file in the confext, and needs the display kernel
# flavour (packages/kernel-display) for a screen.
