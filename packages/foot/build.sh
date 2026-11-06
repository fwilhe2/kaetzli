# Its own terminfo entry (foot, foot-direct), compiled by the builder's tic, since the
# image's trim keeps only a handful. No utmp helper (nothing is setuid), no grapheme
# clustering (utf8proc), no themes or docs. The user units for foot --server go where
# systemd looks, said explicitly rather than read from whichever systemd.pc is found.
meson_install \
    -Dterminfo=enabled \
    -Dutmp-backend=none \
    -Dgrapheme-clustering=disabled \
    -Dthemes=false \
    -Ddocs=disabled \
    -Dtests=false \
    -Dsystemd-units-dir=/usr/lib/systemd/user
# The .desktop files are for application launchers; there is none. Shell completions for
# shells the image does not have, and bash's for a bash-completion it does not ship
# (the base's trim removes the same).
drop_installed usr/share/applications usr/share/zsh usr/share/fish usr/share/bash-completion

# foot refuses to start without a UTF-8 locale ("invalid locale, and failed to find a
# fallback"), and the image is C-locale only (image/build-rootfs.sh's trim). glibc's
# C.UTF-8 is not built in: it is a compiled locale, made here from our glibc's own
# sources in the sysroot (usr/share/i18n), by the builder's localedef — Debian's, of the
# version before ours. The locale files' format has to be the one our glibc reads, which
# test/kiosk.sh checks in the guest (`locale charmap` under LANG=C.UTF-8). Whether the
# base should have C.UTF-8 for everyone is its own decision.
install -d "$ROOTFS/usr/lib/locale"
I18NPATH="$SYSROOT/usr/share/i18n" localedef --no-archive -c -i C -f UTF-8 \
    "$ROOTFS/usr/lib/locale/C.utf8"
