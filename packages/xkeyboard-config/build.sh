# usr/share/xkeyboard-config-2, which libxkbcommon is built to read. No translations (C
# locale), no compatibility rules for the X server or its xorg symlinks.
meson_install -Dnls=false -Dcompat-rules=false -Dxorg-rules-symlinks=false
# The X11/xkb link to it is for X servers; libxkbcommon reads the directory itself.
drop_installed usr/share/X11
