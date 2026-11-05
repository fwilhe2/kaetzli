VERSION="2.10.0"
PACKAGE="expat-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
# Release assets on GitHub, filed under a tag that spells the version with underscores.
URL="https://github.com/libexpat/libexpat/releases/download/R_${VERSION//./_}/${TARBALL}"
SHA256="c624ba2998fc65df1163a49641ff3f9ab73138b11641d92c86bee0fb01b1bc7f"
LICENSE="MIT"
CPE="cpe:2.3:a:libexpat_project:libexpat:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_GITHUB="libexpat/libexpat"
# ...and those tags are what the version check sees, so undo the spelling: R_2_8_2.
UPSTREAM_SED='s/^R_//; s/_/./g'
