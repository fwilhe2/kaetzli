# expat (wayland-scanner's XML) is the base's; libffi comes from DEPENDS. No DTD
# validation (libxml2), no docs, no tests.
meson_install \
    -Ddocumentation=false \
    -Ddtd_validation=false \
    -Dtests=false
