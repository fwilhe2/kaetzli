# --disable-multi-os-directory: lib/, not lib/../lib64 on x86_64. No docs (makeinfo).
./configure --prefix=/usr --disable-static --disable-docs --disable-multi-os-directory
make
make install DESTDIR=$ROOTFS
