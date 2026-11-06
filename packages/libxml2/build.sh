# The library alone: no python bindings, no ICU (WebKit hands it UTF-8), no readline for
# xmllint, which goes with xmlcatalog and xml2-config. configure, not meson: 2.15's
# meson.build runs `git describe`, and the builder has no git.
./configure --prefix=/usr --disable-static --without-python --without-icu \
    --without-readline --without-history
make
make install DESTDIR=$ROOTFS
rm -rf "$ROOTFS/usr/bin"
