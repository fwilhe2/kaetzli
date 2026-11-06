# The list is compiled in (builtin), and IDNA goes through ICU, which the browser has
# anyway. The psl tool is not shipped.
meson_install \
    -Druntime=libicu \
    -Dbuiltin=true \
    -Dtests=false \
    -Ddocs=false
rm -rf "$ROOTFS/usr/bin"
