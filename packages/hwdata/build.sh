# Its configure is a shell script writing Makefile.inc; `make install` copies the files.
./configure --prefix=/usr --datadir=/usr/share
make install DESTDIR=$ROOTFS
drop_installed usr/share/hwdata/pci.ids usr/share/hwdata/usb.ids usr/share/hwdata/oui.txt \
    usr/share/hwdata/iab.txt
