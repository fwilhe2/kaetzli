# The SIMD paths are chosen at run time (cpu-features). No libpng, GTK or OpenMP: those
# serve the demos and tests only.
meson_install \
    -Dlibpng=disabled \
    -Dgtk=disabled \
    -Dopenmp=disabled \
    -Dtests=disabled \
    -Ddemos=disabled
