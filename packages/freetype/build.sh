# zlib for WOFF and compressed PCF fonts, brotli for WOFF2 (most web fonts today, which
# WebKit then decodes through FreeType rather than libwoff2), libpng for colour bitmap
# glyphs (emoji). bzip2 and harfbuzz's auto-hinting of complex scripts stay off.
meson_install \
    -Dzlib=system \
    -Dbzip2=disabled \
    -Dbrotli=enabled \
    -Dpng=enabled \
    -Dharfbuzz=disabled \
    -Dtests=disabled
