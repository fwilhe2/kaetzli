# No text shaping (harfbuzz, utf8proc): the terminal draws one glyph per cell.
meson_install \
    -Dgrapheme-shaping=disabled \
    -Drun-shaping=disabled \
    -Dsvg-backend=nanosvg \
    -Ddocs=disabled \
    -Dexamples=false
