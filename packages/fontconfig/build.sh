# expat (its XML backend) is the base's. No NLS (C locale), no docs, no tests (which
# want bubblewrap and fonts from the internet).
meson_install \
    -Dxml-backend=expat \
    -Dnls=disabled \
    -Ddoc=disabled \
    -Dtests=disabled \
    -Dcache-build=disabled
# The system cache directory, empty: an extension carries /usr and /etc only, and the
# read-only system has no cache to write there; each user caches in ~/.cache/fontconfig.
drop_installed var/cache/fontconfig
rmdir "$ROOTFS/var/cache" "$ROOTFS/var"
