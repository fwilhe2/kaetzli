# EGL only: no GLX, no X11.
meson_install \
    -Degl=yes \
    -Dglx=no \
    -Dx11=false \
    -Dtests=false \
    -Ddocs=false
