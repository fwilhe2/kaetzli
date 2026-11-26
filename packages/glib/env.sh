# GLib, for conmon, which is built on its main loop. Only libglib-2.0 ships — see build.sh.
#
# Coupled to the builder's version the way packages/openssl is: consumers compile against
# sid's libglib2.0-dev headers and resolve against this library by SONAME, and glib's
# symbols are unversioned, so a header newer than the library is a missing symbol at
# runtime that test/check-symbol-versions.py cannot see — test/check-symbol-resolution.py
# is the check that does. Keep this at or above sid's; tools/header-skew.py shows both.
#
# The stable series is the even minor; odd minors are development snapshots, which
# UPSTREAM_IGNORE keeps tools/check-updates.py from proposing.
VERSION="2.90.2"
PACKAGE="glib-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://download.gnome.org/sources/glib/${VERSION%.*}/${TARBALL}"
SHA256="edf473b4f5ee70a31d2410622ee605dc4a2c8c46dbc0ec53c9abbaf3960232fd"
LICENSE="LGPL-2.1-or-later"
CPE="cpe:2.3:a:gnome:glib:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_INDEX="https://download.gnome.org/sources/glib/"
UPSTREAM_SUBDIR='2\.[0-9]*[02468]/'
