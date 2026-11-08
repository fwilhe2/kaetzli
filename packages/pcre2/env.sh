# Perl-compatible regular expressions, here only because glib's GRegex is built on it and
# libglib-2.0 links it unconditionally. Only the 8-bit library is built.
VERSION="10.49"
PACKAGE="pcre2-${VERSION}"
TARBALL="$PACKAGE.tar.bz2"
URL="https://github.com/PCRE2Project/pcre2/releases/download/${PACKAGE}/${TARBALL}"
SIG_URL="${URL}.sig"
SHA256="53c156e1ba416a20da8e65395daa132da0d80e76910424caca3fcdae7831d384"
LICENSE="BSD-3-Clause WITH PCRE2-exception"
CPE="cpe:2.3:a:pcre:pcre2:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="PCRE2Project/pcre2"
UPSTREAM_SED='s/^pcre2-//'
