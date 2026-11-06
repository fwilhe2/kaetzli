# libsqlite3; the sqlite3 shell is not shipped. No readline/editline either way.
# --soname=legacy: since 3.48 the build gives the library no SONAME unless asked, and a
# consumer then records the path it linked against (the dependency sysroot) as NEEDED.
# legacy is libsqlite3.so.0, what every distribution ships. No RUNPATH (/usr/lib).
./configure --prefix=/usr --disable-static --disable-readline --disable-editline \
    --soname=legacy --disable-rpath
make
make install DESTDIR=$ROOTFS
rm -rf "$ROOTFS/usr/bin"
