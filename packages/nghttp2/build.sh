# The library alone: the nghttp, nghttpd and h2load tools want libev and more.
./configure --prefix=/usr --disable-static --enable-lib-only
make
make install DESTDIR=$ROOTFS
rm -rf "$ROOTFS/usr/bin" "$ROOTFS/usr/share/nghttp2"
