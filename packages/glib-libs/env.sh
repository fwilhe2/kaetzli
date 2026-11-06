# The rest of GLib — gio, gobject, gmodule, gthread — which the base leaves out
# (packages/glib/build.sh: nothing there loads them). The browser stack is built on all
# four. Same tarball and options as the base's glib; ships only what that one deletes.
source packages/glib/env.sh
FOLLOWS=glib
EXTENSION=kiosk
DEPENDS="libffi"
# The base's glib carries the CPE; one package, one count.
unset CPE
