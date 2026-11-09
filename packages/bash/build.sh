# Non-login interactive shells read /etc/bashrc (upstream's default is no system bashrc).
# Appended to CFLAGS, which build-package.sh has set: configure keeps what it is given.
./configure --prefix=/usr CFLAGS="$CFLAGS -DSYS_BASHRC='\"/etc/bashrc\"'"
make
make install DESTDIR=$ROOTFS

# /bin/sh, which upstream leaves to the distribution: bash is the only shell here, and
# Fedora and Arch point sh at it the same way. Invoked as sh it reads sh's startup files
# but is not in POSIX mode.
ln -sf bash $ROOTFS/usr/bin/sh

# The loadable builtins (examples, mostly duplicating coreutils; Debian ships them
# separately) and bashbug, which needs a sendmail.
drop_installed usr/lib/bash bashbug
