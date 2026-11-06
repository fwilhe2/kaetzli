# Every optional codec off by name: zlib and bzip2 for compressed PCF fonts, brotli for
# WOFF2, libpng for colour bitmap glyphs, harfbuzz for auto-hinting of complex scripts.
# The shipped fonts are plain TrueType.
meson_install \
    -Dzlib=disabled \
    -Dbzip2=disabled \
    -Dbrotli=disabled \
    -Dpng=disabled \
    -Dharfbuzz=disabled \
    -Dtests=disabled
