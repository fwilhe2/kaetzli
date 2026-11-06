# Every optional dependency off by name, so a newer builder cannot add one:
#   renderers=[]   pixman only (GLES2 and Vulkan need mesa)
#   allocators=[]  DRM dumb buffers only (GBM is mesa's)
#   backends       DRM and libinput; not the nested X11 one
#   xwayland, xcb-errors  no X11 clients
#   color-management (lcms2), libliftoff (plane offload, for real GPUs)
meson_install \
    -Drenderers=[] \
    -Dallocators=[] \
    -Dbackends=drm,libinput \
    -Dsession=enabled \
    -Dxwayland=disabled \
    -Dxcb-errors=disabled \
    -Dcolor-management=disabled \
    -Dlibliftoff=disabled \
    -Dexamples=false
assert_not_linked libGL usr/lib/libwlroots-0.20.so
assert_not_linked libEGL usr/lib/libwlroots-0.20.so
assert_not_linked libgbm usr/lib/libwlroots-0.20.so
