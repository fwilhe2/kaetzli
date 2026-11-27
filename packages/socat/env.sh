# socat: a bidirectional pipe between any two of TCP, UDP, unix sockets, vsock, ptys and
# files. Here for talking to the sockets things on this image listen on — podman's API,
# containerd's, systemd's varlink services — and for the vsock a Firecracker or
# Virtualization.framework host reaches the guest through. curl covers HTTP over a unix
# socket; socat is everything that is not HTTP. C, against the OpenSSL the base ships.
VERSION="1.8.1.3"
PACKAGE="socat-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="http://www.dest-unreach.org/socat/download/${TARBALL}"
SHA256="06602ffd591e98c75b3dc1d66f0f19136cc666b0b2d95caad987d6ab2cb28097"
LICENSE="GPL-2.0-only"
CPE="cpe:2.3:a:dest-unreach:socat:${VERSION}:*:*:*:*:*:*:*"
# In the debug system extension (docs/guides/debugging.md).
EXTENSION=debug
