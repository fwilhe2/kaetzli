# The data directory is said explicitly: by default it comes from xkeyboard-config.pc,
# which is not a dependency here, and in a dependency sysroot would name the sysroot.
# No X11 (libxcb), no xkbregistry (libxml2), no tools, which are for debugging keymaps.
# The compose tables are libX11's, which nothing ships: a client asking for one gets
# none, and dead keys do not compose.
meson_install \
    -Dxkb-config-root=/usr/share/xkeyboard-config-2 \
    -Dx-locale-root=/usr/share/X11/locale \
    -Denable-x11=false \
    -Denable-wayland=false \
    -Denable-xkbregistry=false \
    -Denable-tools=false \
    -Denable-docs=false \
    -Denable-bash-completion=false
