./configure --prefix=/usr --disable-static
make
make install DESTDIR=$ROOTFS
# mtdev-test prints a device's events, for debugging the library.
drop_installed mtdev-test
