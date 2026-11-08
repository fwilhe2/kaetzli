# The HTTP client, and the reason the TLS stack above it is here. `curl https://...` from
# inside the guest is half of what issue #77 asks for; the other half is `ip`.
#
# There is no second candidate worth listing. wget2 and httpie are the alternatives and
# neither is close: wget2 drags in its own TLS choice plus libpsl and gettext, httpie is
# python. curl is C, and it is in every distribution.
VERSION="8.23.0"
PACKAGE="curl-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://curl.se/download/${TARBALL}"
SIG_URL="${URL}.asc"
SHA256="8dadd7d2536a314f982ad05a651b713f426bf7d02cc2c76cffb9796f2218e6c5"
# curl's own license: MIT with the "not to be used in advertising" clause, i.e. the same
# shape as X11. SPDX gives it an identifier of its own because the text is not quite
# either of them.
LICENSE="curl"
CPE="cpe:2.3:a:haxx:curl:${VERSION}:*:*:*:*:*:*:*"
