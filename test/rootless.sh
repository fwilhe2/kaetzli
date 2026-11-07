#!/usr/bin/env bash
# Boot the image, log in as the unprivileged `user`, and run containers without root.
#
#     ./test/rootless.sh [rootfs.erofs] [bzImage]
#
# Overrides: TIMEOUT, MEM/CPUS, LOG, as in test/podman.py. LOGIN_USER is fixed to the
# uid-1000 account image/files/etc/passwd ships, because that is the point.
#
# Rootless needs three things the rootful path does not, and each round checks one:
#   1. newuidmap and newgidmap with their file capabilities on the disk image — getcap
#      reads them off the mounted root, which proves they survived the tar mkfs.erofs is
#      built from (image/build-rootfs.sh) — and podman reporting itself rootless.
#   2. the ID maps: a container's /proc/self/uid_map has two lines, the user's own uid
#      and the 65536 from /etc/subuid. Only newuidmap can write the second, so one line
#      means the helper did not run or was not allowed to.
#   3. the network, through pasta rather than netavark's bridge: a name resolves and a TCP
#      connection opens from inside the container, and a published port's forwarding is
#      set up (rootlessport).
#
# The same imported test image as test/podman.py, made by the user into the user's own
# storage under ~/.local/share/containers.
set -euo pipefail

cd "$(dirname "$0")/.."
source test/qemu-lib.sh

ROOTFS="${1:-${ROOTFS:-output/rootfs.erofs}}"
KERNEL="${2:-${KERNEL:-rootfs/boot/bzImage}}"
# podman is a system extension (docs/guides/sysext.md), attached as a read-only disk the way the
# root is: extensions/podman.raw next to the image (qemu_sysexts in test/qemu-lib.sh).
SYSEXTS="${SYSEXTS:-podman}"
INIT="${INIT:-/usr/lib/systemd/systemd}"
LOG="${LOG:-output/rootless-test.log}"
TEST_NAME=rootless
LOGIN_USER=user

CAPS_OK="ROOTLESS-CAPS-OK"
INFO="ROOTLESS-INFO:"
IMPORT_OK="ROOTLESS-IMPORT-OK"
MAP_OK="ROOTLESS-MAP-OK"
NET_OK="ROOTLESS-NET-OK"

# getcap prints `<path> cap_setuid=ep`; matching on the capability rather than the whole
# line keeps this from depending on getcap's spacing.
CAPS="[[ \$(getcap /usr/bin/newuidmap) == *cap_setuid=ep ]] && [[ \$(getcap /usr/bin/newgidmap) == *cap_setgid=ep ]] && \
[[ \$(id -u) == 1000 ]] && echo ROOTLESS'-CAPS-OK'"

INFO_CHECK="echo ROOTLESS'-INFO':\$(podman info --format '{{.Host.Security.Rootless}}/{{.Host.OCIRuntime.Name}}/{{.Store.GraphDriverName}}')"

IMPORT="rm -rf ~/img && mkdir -p ~/img/usr/bin ~/img/usr/lib && \
cp -L /usr/bin/bash /usr/bin/getent ~/img/usr/bin/ && \
cp -L /usr/lib/libc.so.6 /usr/lib/libtinfo.so.6 /usr/lib/ld-linux-* ~/img/usr/lib/ && \
ln -s usr/bin ~/img/bin && ln -s usr/lib ~/img/lib && ln -s usr/lib ~/img/lib64 && \
tar -C ~/img -c . | podman import - localhost/mini >/dev/null && echo ROOTLESS'-IMPORT-OK'"

# Two lines in uid_map: `0 1000 1` and `1 100000 65536`.
MAP="podman run --rm localhost/mini /usr/bin/bash -c 'n=0; while read -r a b c; do echo map: \$a \$b \$c; n=\$((n+1)); done < /proc/self/uid_map; [[ \$n == 2 ]] && echo \"ROOTLESS-MAP\"\"-OK\"'"

NET="podman run --rm -p 8080:80 localhost/mini /usr/bin/bash -c 'getent hosts example.com && exec 3<>/dev/tcp/example.com/80 && echo \"ROOTLESS-NET\"\"-OK\"'"

DIAGNOSE="id; getcap /usr/bin/newuidmap /usr/bin/newgidmap; cat /etc/subuid; podman info 2>&1 | head -60
echo \$XDG_RUNTIME_DIR; systemctl --user --no-pager status | head -20; journalctl --no-pager -b -n 40 -o short"

qemu_setup
qemu_preflight
qemu_boot
console_login

echo ">> logged in as $LOGIN_USER, checking the ID-map helpers"
console_send "$CAPS"
await "$CAPS_OK" 60 || fail "newuidmap/newgidmap lack their file capabilities on the image, or this is not uid 1000"
echo ">> helpers OK: newuidmap cap_setuid, newgidmap cap_setgid, as uid 1000"

mark=$(console_mark)
console_send "$INFO_CHECK"
await "$INFO[a-z]" 120 "$mark" || fail "podman info did not answer as $LOGIN_USER"
info=$(tail -c +"$mark" "$LOG" | tr -d '\r' | sed -n "s/.*${INFO}\\([^[:space:]]*\\).*/\\1/p" | tail -1)
[ "$info" = "true/crun/overlay" ] ||
    fail "podman reports rootless/runtime/storage as '$info', expected true/crun/overlay"
echo ">> podman OK: $info"

console_send "$IMPORT"
await "$IMPORT_OK" 120 || fail "could not import a test image into the user's storage"

echo ">> running a rootless container"
console_send "$MAP"
await "$MAP_OK" 120 || fail "the container did not get the subordinate ID range (newuidmap did not map it)"
echo ">> ID maps OK: uid 1000 and the 65536 subordinate IDs"

echo ">> reaching the network from a rootless container"
console_send "$NET"
await "$NET_OK" 120 || fail "no DNS or TCP from a rootless container, or the published port was refused"
echo ">> network OK: through pasta, with a port published"
