# libharfbuzz with its FreeType and ICU integrations, which WebKit asks for. No glib or
# gobject bindings, no cairo, no command-line tools (hb-view, hb-shape...).
meson_install \
    -Dfreetype=enabled \
    -Dicu=enabled \
    -Dglib=disabled \
    -Dgobject=disabled \
    -Dcairo=disabled \
    -Dchafa=disabled \
    -Dgraphite2=disabled \
    -Dintrospection=disabled \
    -Ddocs=disabled \
    -Dtests=disabled \
    -Dutilities=disabled \
    -Dbenchmark=disabled
