#!/usr/bin/env bash
# Prove that dm-verity is enforcing, not merely present: tamper with one byte of a copy of
# the image and check that the kernel refuses to mount it.
#
#     ./test/verity.sh [rootfs.erofs] [bzImage]
#
# The byte is the first one in the file, in the 1 KiB EROFS reserves ahead of its
# superblock for a boot sector it never has. EROFS does not read it as data, so without
# verity the tampered image boots exactly like the original — and that is what makes the
# choice sharp rather than lazy: the only thing that can notice is a hash over the whole
# 4 KiB block it sits in. Changing a byte inside a file would also fail, but would leave
# open whether the filesystem or verity caught it.
#
# Two things have to be true: the console carries device-mapper's corruption report, and
# the guest never gets as far as a shell. test/boot.py boots the same image untampered
# with the same INIT, which is the control: if that passes and this fails the other way,
# the difference is the byte.
set -euo pipefail

cd "$(dirname "$0")/.."
source test/qemu-lib.sh

ORIGINAL="${1:-${ROOTFS:-output/rootfs.erofs}}"
KERNEL="${2:-${KERNEL:-rootfs/boot/bzImage}}"
INIT=/bin/bash
LOG="${LOG:-output/verity-test.log}"
TEST_NAME=verity

# The copy goes next to the console log, and is deleted once the guest has stopped; a
# failure that exits before then leaves it there to look at. Its kernel arguments are the
# original's, which is the whole point: a root hash that describes different bytes.
ROOT_CMDLINE="${ROOT_CMDLINE:-$(dirname "$ORIGINAL")/rootfs.cmdline}"
ROOTFS="$(dirname "$LOG")/verity-tampered.erofs"

qemu_setup
ROOTFS="$ORIGINAL" qemu_preflight

cp --reflink=auto "$ORIGINAL" "$ROOTFS"
old=$(od -An -tu1 -N1 "$ROOTFS" | tr -d ' ')
new=$(( old ^ 0xff ))
printf "\\x$(printf '%02x' "$new")" | dd of="$ROOTFS" bs=1 seek=0 count=1 conv=notrunc status=none
echo ">> flipped byte 0 of a copy of $ORIGINAL ($old -> $new)"

# The corruption is the point, so its report is no reason to fail, and neither is the
# stack the kernel prints with its panic over the root it then cannot mount.
CONSOLE_BAD_EXEMPT='device-mapper: verity|Call Trace:'
qemu_boot

# What the kernel says when a block fails its hash, and what a shell would print.
CORRUPT='device-mapper: verity.*(corrupted|verification failed)'
PROMPT='bash-[0-9]'

status=timeout
while [ "$SECONDS" -lt "$deadline" ]; do
    if grep -qE "$PROMPT" "$LOG"; then
        status=shell
        break
    fi
    if grep -qE "$QEMU_DIED" "$LOG" || ! kill -0 "$qemu_pid" 2>/dev/null; then
        status=died
        break
    fi
    sleep 2
done
kill "$qemu_pid" 2>/dev/null || true
rm -f "$ROOTFS"

if [ "$status" = shell ]; then
    fail "a tampered image booted to a shell — dm-verity is not checking the root"
fi
if ! grep -qE "$CORRUPT" "$LOG"; then
    fail "the tampered image did not boot, but not because verity said so ($status)"
fi

echo ">> verity OK: the tampered image was refused"
grep -E "$CORRUPT|Kernel panic|VFS" "$LOG" | tr -d '\r' | head -4 | sed 's/^/   /'
