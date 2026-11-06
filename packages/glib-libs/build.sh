# The base's glib build, again, keeping what packages/glib/build.sh deletes: libgio,
# libgobject, libgmodule and libgthread, and GIO's module directory, where
# glib-networking puts its TLS backend. libglib-2.0 and everything else is the base's.
# Same options, so the libraries are the same build as the one they load beside.
meson_install \
    -Dselinux=disabled \
    -Dxattr=false \
    -Dlibmount=disabled \
    -Dlibelf=disabled \
    -Dsysprof=disabled \
    -Ddtrace=disabled \
    -Dsystemtap=disabled \
    -Dnls=disabled \
    -Dtests=false \
    -Dinstalled_tests=false \
    -Ddocumentation=false \
    -Dman-pages=disabled \
    -Dintrospection=disabled

keep=$(mktemp -d)
for lib in gio gobject gmodule gthread; do
    cp -a "$ROOTFS"/usr/lib/lib$lib-2.0.so* "$keep/"
done
rm -rf "${ROOTFS:?}"/usr
install -d "$ROOTFS/usr/lib"
cp -a "$keep"/* "$ROOTFS/usr/lib/"
rm -rf "$keep"
