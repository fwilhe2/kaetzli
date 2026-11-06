# The library; no doc, no dumpsexp/hmac256/mpicalc tools.
#
# configure asks gpg-error-config (or gpgrt-config) about libgpg-error, and
# packages/libgpg-error ships neither: they are link-time wrappers. A shim answers the
# same questions from pkg-config, which reads the dependency sysroot's gpg-error.pc.
cat > /tmp/gpg-error-config <<'SH'
#!/bin/sh
case "$*" in
    --version|--modversion) exec pkg-config --modversion gpg-error ;;
    --cflags|"--mt --cflags") exec pkg-config --cflags gpg-error ;;
    --libs|"--mt --libs") exec pkg-config --libs gpg-error ;;
    --variable=*) exec pkg-config "$1" gpg-error ;;
    --host) exec pkg-config --variable=host gpg-error ;;
esac
exit 1
SH
chmod +x /tmp/gpg-error-config
GPG_ERROR_CONFIG=/tmp/gpg-error-config ./configure --prefix=/usr --disable-static --disable-doc
make
make install DESTDIR=$ROOTFS
# libgcrypt.la records the dependency sysroot it was linked in; nothing uses libtool archives.
rm -rf "$ROOTFS/usr/bin" "$ROOTFS/usr/lib/libgcrypt.la"
