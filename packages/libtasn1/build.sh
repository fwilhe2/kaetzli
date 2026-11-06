# The library; asn1Coding, asn1Decoding and asn1Parser are developer tools.
./configure --prefix=/usr --disable-static --disable-doc
make
make install DESTDIR=$ROOTFS
rm -rf "$ROOTFS/usr/bin"
