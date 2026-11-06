# The OpenSSL backend only, no GnuTLS; no proxy modules, which ask libproxy or GNOME's
# settings for a proxy the kiosk does not have.
meson_install \
    -Dgnutls=disabled \
    -Dopenssl=enabled \
    -Dlibproxy=disabled \
    -Dgnome_proxy=disabled \
    -Dinstalled_tests=false
rm -rf "$ROOTFS/usr/share/locale"
