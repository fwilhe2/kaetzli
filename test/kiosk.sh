#!/usr/bin/env bash
# Boot the display kernel with the kiosk extension attached and a virtual screen, and
# check that the session really is on that screen and takes keyboard input:
#
#     ./test/kiosk.sh [rootfs.erofs] [bzImage-display]
#
# Overrides: TIMEOUT, MEM/CPUS, LOG, as in test/container.sh. KEEP=1 leaves the
# screenshots in output/kiosk-test/.
#
# What the guest has to show, in order:
#   - the display kernel (uname -r ends in -display) and a DRM device
#   - kiosk.service active, as a logind session of the kiosk user on seat0, tty1,
#     of type wayland (beside the session of the user's own manager, which has no seat) — the session libseat takes the screen and input from
#   - foot running under cage, and a boot that is `running`, nothing failed
#   - the C.UTF-8 locale foot needs loads: compiled by the builder's localedef
#     (packages/foot), its format has to be the one our glibc reads
#   - a screen that is not blank: qemu's screendump, taken through the monitor, has to
#     have more than a handful of colours, which only rendered text gives
#   - keys reaching the terminal: `touch /tmp/kiosk-typed`, typed with the monitor's
#     sendkey into the virtio keyboard, has to make a file owned by kiosk — keyboard,
#     libinput, wlroots, cage, foot and the kiosk user's shell, end to end
#
# The console is serial, as in every other test; the screen goes nowhere
# (QEMU_GRAPHICS=1). The monitor is a second one, on a pipe chardev
# (<path>.in and <path>.out fifos), so bash can drive it without a socket client.
set -euo pipefail

cd "$(dirname "$0")/.."
source test/qemu-lib.sh

ROOTFS="${1:-${ROOTFS:-output/rootfs.erofs}}"
KERNEL="${2:-${KERNEL:-rootfs/boot/bzImage-display}}"
SYSEXTS="${SYSEXTS:-kiosk}"
INIT="${INIT:-/usr/lib/systemd/systemd}"
LOG="${LOG:-output/kiosk-test.log}"
TEST_NAME=kiosk
QEMU_GRAPHICS=1

DIAGNOSE="systemd-sysext status; systemctl --no-pager status kiosk.service kiosk-udev.service
loginctl --no-pager list-sessions; ls -l /dev/dri; journalctl --no-pager -b -u kiosk.service -n 40"

scratch=$(realpath -m output/kiosk-test)   # absolute: qemu resolves screendump's path
rm -rf "$scratch"
mkdir -p "$scratch"
mkfifo "$scratch/monitor.in" "$scratch/monitor.out"
# shellcheck disable=SC2054 # qemu's own comma-separated options, one argument each
QEMU_DISPLAY_ARGS=(-nographic -chardev "pipe,id=mon,path=$scratch/monitor" -mon chardev=mon,mode=readline)

qemu_setup
qemu_preflight
qemu_boot
# Re-armed over qemu_boot's: the fifo reader goes with the guest, the scratch directory
# unless KEEP=1.
trap 'rc=$?; kill "${monitor_reader:-}" 2>/dev/null || true; cleanup "$rc"
      [ "${KEEP:-0}" = 1 ] || rm -rf "$scratch"' EXIT

# qemu opens both fifos read-write, so neither side blocks; its replies are drained so
# a full pipe never stalls it.
cat "$scratch/monitor.out" > "$scratch/monitor.log" &
monitor_reader=$!
exec 4<> "$scratch/monitor.in"
monitor() { printf '%s\n' "$1" >&4; }

# sendkey names for what `touch /tmp/kiosk-typed` is made of.
type_text() {
    local text=$1 i c key
    for ((i = 0; i < ${#text}; i++)); do
        c=${text:i:1}
        case "$c" in
            [a-z0-9]) key=$c ;;
            ' ') key="spc" ;;
            /) key="slash" ;;
            -) key="minus" ;;
            .) key="dot" ;;
            *) fail "type_text: no key for '$c'" ;;
        esac
        monitor "sendkey $key"
        sleep 0.1
    done
    monitor "sendkey ret"
}

# screenshot <name> — a PPM in $scratch, and how many distinct byte values its pixels
# have. A blank screen has one or two; rendered, antialiased text has dozens.
screenshot() {
    local file="$scratch/$1.ppm" n
    rm -f "$file"
    monitor "screendump $file"
    for _ in $(seq 20); do
        [ -s "$file" ] && break
        sleep 0.5
    done
    [ -s "$file" ] || fail "screendump wrote nothing to $file"
    [ "$(head -c 2 "$file")" = P6 ] || fail "$file is not a PPM"
    # The pixel data follows the three header lines.
    n=$(tail -n +4 "$file" | od -An -v -tu1 | tr -s ' ' '\n' | sort -u | grep -c .)
    echo "$n"
}

console_login

check() { # name command — the command prints KIOSK-<name>-OK on success
    local mark
    echo ">> $1"
    mark=$(console_mark)
    console_send "$2"
    await "KIOSK-$1-OK" 120 "$mark" || fail "$1"
}

check KERNEL "uname -r | grep -q -- '-display\$' && test -c /dev/dri/card0 && echo KIOSK-KERNEL'-OK'"
check SESSION "systemctl is-active --quiet kiosk.service && s=\$(loginctl --no-legend list-sessions | awk '\$3 == \"kiosk\" && \$4 == \"seat0\" { print \$1 }') && [ -n \"\$s\" ] && [ \"\$(loginctl show-session \"\$s\" -p Seat --value)\" = seat0 ] && [ \"\$(loginctl show-session \"\$s\" -p TTY --value)\" = tty1 ] && [ \"\$(loginctl show-session \"\$s\" -p Type --value)\" = wayland ] && echo KIOSK-SESSION'-OK'"
check CLIENT "for i in \$(seq 60); do pgrep -u kiosk -x foot >/dev/null && break; sleep 1; done; pgrep -u kiosk -x cage >/dev/null && pgrep -u kiosk -x foot >/dev/null && echo KIOSK-CLIENT'-OK'"
check LOCALE "[ \"\$(LANG=C.UTF-8 locale charmap 2>&1)\" = UTF-8 ] && echo KIOSK-LOCALE'-OK'"
check RUNNING "[ \"\$(systemctl is-system-running --wait)\" = running ] && echo KIOSK-RUNNING'-OK'"

# foot draws its first frame once the shell has started; give it a moment.
sleep 5
echo ">> SCREEN"
colours=$(screenshot screen)
echo ">> the screen has $colours distinct values"
[ "$colours" -gt 8 ] || fail "the screen is blank ($colours distinct values in $scratch/screen.ppm)"

echo ">> KEYBOARD"
type_text "touch /tmp/kiosk-typed"
check KEYBOARD "for i in \$(seq 30); do [ -e /tmp/kiosk-typed ] && break; sleep 1; done; [ \"\$(stat -c %U /tmp/kiosk-typed)\" = kiosk ] && echo KIOSK-KEYBOARD'-OK'"
screenshot typed >/dev/null

echo ">> kiosk OK: display kernel, logind session on seat0, cage and foot on screen, keyboard input"
