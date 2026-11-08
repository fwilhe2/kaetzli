# gdbserver, out of the gdb source tree, and only gdbserver: the half of gdb that runs
# beside the program being debugged. The other half — symbols, source, the user
# interface — runs on the host, which has the debug info and an editor where this
# machine has neither. A VM is the case gdbserver exists for, and it avoids everything
# full gdb would bring: GMP and MPFR at runtime, its embedded Python unless configured
# out, and many times the size. docs/guides/debugging.md has the recipe.
VERSION="18.1"
PACKAGE="gdb-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://ftp.gnu.org/gnu/gdb/${TARBALL}"
SIG_URL="${URL}.sig"
SHA256="cd9fc3fe2b47743840e42c1592d3d87f8302eb18639c0b8b4ba0898002e2348f"
LICENSE="GPL-3.0-or-later"
CPE="cpe:2.3:a:gnu:gdb:${VERSION}:*:*:*:*:*:*:*"
# In the debug system extension (docs/guides/debugging.md).
EXTENSION=debug
