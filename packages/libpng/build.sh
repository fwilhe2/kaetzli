# The library only: pngfix and png-fix-itxt are repair tools, libpng-config a wrapper
# for linking against it.
./configure --prefix=/usr --disable-static --disable-tools
make
make install DESTDIR=$ROOTFS
rm -rf "$ROOTFS/usr/bin"
