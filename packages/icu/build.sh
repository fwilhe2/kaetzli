# ICU's build is in source/. The tools (genrb, pkgdata, icupkg...) are built because the
# data library is packaged with them, then deleted: they compile data, which nothing
# here does at run time. No samples, tests or the layout extension.
cd source
./configure --prefix=/usr --disable-static --disable-tests --disable-samples \
    --disable-extras --disable-layoutex
make
make install DESTDIR=$ROOTFS
# usr/lib/icu is the Makefile fragment and pkgdata.inc for building against ICU;
# usr/share/icu its install scripts and a copy of the license, which ships from the
# tarball anyway.
rm -rf "$ROOTFS/usr/bin" "$ROOTFS/usr/sbin" "$ROOTFS/usr/lib/icu" "$ROOTFS/usr/share/icu"
