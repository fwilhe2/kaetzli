mkdir -p build
pushd build
# --disable-werror: sid's linux-libc-dev redefines OPEN_TREE_CLONE etc. that glibc's
# own sys/mount.h also defines, which -Werror turns into a fatal build error.
# The hardening every other package gets from builder/build-package.sh, which glibc
# (NO_SYSROOT) does not see, by glibc's own switches, as Fedora configures it: a stack
# protector, fortified internal calls, and BIND_NOW for the loader and every library.
../configure --prefix=/usr --disable-werror \
    --enable-stack-protector=strong --enable-fortify-source --enable-bind-now
make
make install DESTDIR=$ROOTFS
popd

# Generators, the malloc/loader debugging tools, the timezone compiler (packages/tzdata
# ships compiled data) and a static ln for repairing systems. Kept: ldconfig, ldd, iconv,
# locale, getent, getconf.
drop_installed gencat makedb iconvconfig localedef mtrace sotruss sprof pcprofiledump \
               xtrace pldd sln zic zdump tzselect

# What those tools preload, and nothing else does: sotruss's audit module, pcprofiledump's
# profiler and memusage's counter (memusage itself is not installed: it needs libgd).
# libBrokenLocale is for programs linked with -lBrokenLocale, of which there are none.
# libc_malloc_debug stays — MALLOC_CHECK_ needs it preloaded — and so do the compatibility
# stubs (libpthread, libdl, librt, libutil, libanl, libnsl) that binaries built against an
# older glibc name in NEEDED.
drop_installed usr/lib64/audit/sotruss-lib.so usr/lib/libpcprofile.so usr/lib/libmemusage.so \
               usr/lib/libBrokenLocale.so.1 usr/lib64/libBrokenLocale.so

# nscd cannot start (it links libselinux) and nsswitch.conf does not use it. Unchecked:
# which of its files install depends on the build.
rm -f $ROOTFS/usr/bin/nscd
rm -f $ROOTFS/etc/nscd.conf
rm -f $ROOTFS/usr/lib/systemd/system/nscd.service
rm -f $ROOTFS/usr/lib/tmpfiles.d/nscd.conf
