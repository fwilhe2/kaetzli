# strace: every system call a process makes, with its arguments decoded. The first tool
# to reach for when something fails with an errno and no message — which, on an image
# whose /etc is read-only and whose /var starts empty, is most of what goes wrong. C, no
# dependencies once its optional stack unwinder is configured out (build.sh).
VERSION="7.3"
PACKAGE="strace-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://github.com/strace/strace/releases/download/v${VERSION}/${TARBALL}"
SIG_URL="${URL}.asc"
SHA256="529634c40910895a9a1f3c5c1e65da2ca9ed72fb158dfc4bcc89efdf05e39b72"
LICENSE="LGPL-2.1-or-later"
CPE="cpe:2.3:a:strace_project:strace:${VERSION}:*:*:*:*:*:*:*"
# In the debug system extension, not the image (EXTENSION, see build.sh and
# docs/guides/debugging.md): a machine that is working needs none of these tools, and one that
# is not gets them by attaching a disk rather than by being rebuilt.
EXTENSION=debug
UPSTREAM_GITHUB="strace/strace"
UPSTREAM_SED='s/^v//'
