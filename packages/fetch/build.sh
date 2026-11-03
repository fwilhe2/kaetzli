# One C file against glibc and nothing else, so there is no configure step and no build
# system to drive — the compile is the whole build.
#
# Two things are different from every other package here, both because of LOCAL_SOURCE:
#
#   - /usr/local/src is bind-mounted **read-only**. It is this repository's working tree
#     rather than a tarball unpacked into a gitignored directory, and a build that left
#     object files in it would show up as uncommitted changes. Hence one gcc invocation
#     straight to DESTDIR: no intermediate .o, no build directory, nothing to clean up.
#   - There is no ./configure to find an optional Debian library and link against it, so
#     the "linked against a library only the builder image has" trap does not apply. What
#     this links is what is written here.
#
# $CPPFLAGS/$CFLAGS/$LDFLAGS carry the --sysroot that points gcc at our staged glibc
# instead of the builder image's (builder/build-package.sh). They are passed through
# rather than replaced, which is the whole of what CLAUDE.md warns about for build
# systems that overwrite them.
install -d $ROOTFS/usr/bin

gcc $CPPFLAGS $CFLAGS $LDFLAGS \
    -std=gnu11 -O2 -Wall -Wextra \
    -o $ROOTFS/usr/bin/fetch \
    fetch.c

# The names people type out of habit. Relative, so they resolve inside any root the tree
# is mounted at; the program does not look at argv[0], so all three behave the same.
for alias in neofetch fastfetch; do
    ln -s fetch $ROOTFS/usr/bin/$alias
done
