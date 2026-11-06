# lua-plugins off by name: Lua in the image is constraint 5, and libinput 1.30 started
# embedding it. No libwacom (a database of real tablets), no debug GUI (GTK), no tests.
# The udev rules and quirks go under /usr, which the sysext carries.
meson_install \
    -Dlua-plugins=disabled \
    -Dlibwacom=false \
    -Ddebug-gui=false \
    -Dtests=false \
    -Dinstall-tests=false \
    -Ddocumentation=false \
    -Dzshcompletiondir=no \
    -Dudev-dir=/usr/lib/udev
# The libinput tool's helpers that are python scripts (measure, analyze, replay, record's
# companions), constraint 5. The C ones (list-devices, debug-events) stay.
python_tools=$(grep -l '^#!.*python' "$ROOTFS"/usr/libexec/libinput/* || true)
[ -n "$python_tools" ] || { echo "error: no python helpers found in usr/libexec/libinput — stale removal" >&2; exit 1; }
rm -f $python_tools
assert_not_linked liblua usr/lib/libinput.so.10
