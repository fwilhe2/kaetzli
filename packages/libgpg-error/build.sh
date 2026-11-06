# The library. Its tools (gpg-error, yat2m) and gpgrt-config go; libgcrypt's configure
# finds it through pkg-config instead.
./configure --prefix=/usr --disable-static --disable-nls --disable-doc --disable-tests \
    --disable-languages
make
make install DESTDIR=$ROOTFS
rm -rf "$ROOTFS/usr/bin" "$ROOTFS/usr/share/common-lisp" "$ROOTFS/usr/share/libgpg-error"
