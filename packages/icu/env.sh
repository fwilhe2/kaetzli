# ICU: Unicode text segmentation, collation, normalisation and conversion, which WebKit
# requires outright (and harfbuzz's ICU integration, which WebKit asks for).
VERSION="79.1"
PACKAGE="icu-${VERSION}"
TARBALL="icu4c-${VERSION}-sources.tgz"
URL="https://github.com/unicode-org/icu/releases/download/release-${VERSION}/${TARBALL}"
SHA256="06fa63ecb521048f8082f12ad988f6b678d90cbd88aafce24cbc94902b191d99"
LICENSE="Unicode-3.0"
CPE="cpe:2.3:a:unicode:international_components_for_unicode:${VERSION}:*:*:*:*:c\/c\+\+:*:*"
UPSTREAM_GITHUB="unicode-org/icu"
EXTENSION=kiosk
