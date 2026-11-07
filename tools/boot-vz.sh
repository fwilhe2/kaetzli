#!/usr/bin/env bash
# Boot the image with Apple's Virtualization.framework (macOS on Apple silicon), console
# in this terminal, through vfkit — the command-line front end podman machine uses.
#
#     brew install vfkit
#     ARCH=arm64 ./tools/fetch-image.sh     # or build it; see README
#     ./tools/boot-vz.sh
#     ./tools/ssh.sh                        # from another terminal, over vsock
#
# Overrides, as for tools/boot-qemu.py:
#   OUT, KERNEL, ROOTFS, INIT, MEM, CPUS, SSH, SSH_DIR, VAR_DISK, VAR_SIZE
#   VFKIT    the binary                         (default: vfkit on PATH)
#   SHARE    a host directory to share; in the guest: mount -t virtiofs share /mnt
#
# Untested by CI, which has no Mac: test/vmm.py boots the image with the same devices on
# qemu (QEMU_PROFILE=vz) — an hvc0 console with no UART, virtiofs and ssh over vsock — and
# packages/kernel/build.sh's guest.config is what makes them work. The vfkit flags below
# are the part that has not been run, and are each marked where that matters.
#
# What it boots is the image every other script boots, unchanged: rootfs.erofs, the
# kernel arguments from rootfs.cmdline that carry its dm-verity root hash, and arm64's
# kernel, which is an uncompressed Image — what Virtualization.framework's Linux boot
# loader takes — staged under the name bzImage like amd64's. Intel Macs are not
# supported: the amd64 kernel is a bzImage, and nothing here has been tried with it.
#
# The network is Virtualization.framework's NAT, which hands out addresses over DHCP, so
# the image's own DHCP-on-everything networkd configuration is all the guest needs. ssh
# goes over vsock rather than through it, as with tools/boot-firecracker.sh: systemd's ssh
# generator puts sshd on vsock port 22 whenever the guest has a vsock device, and vfkit
# forwards a Unix socket on the host to that port.
#
# This script and the parts of test/qemu-lib.sh it uses run on macOS's bash 3.2 and BSD
# userland. Leave the guest with `poweroff`.
set -euo pipefail

cd "$(dirname "$0")/.."
source test/qemu-lib.sh

[ "$(uname -s)" = Darwin ] || { echo "error: Virtualization.framework is macOS-only; on Linux use tools/boot-qemu.py or tools/boot-firecracker.sh" >&2; exit 1; }
ARCH=$(normalize_arch "${ARCH:-}")
[ "$ARCH" = arm64 ] || { echo "error: only Apple silicon (arm64) is supported" >&2; exit 1; }
fetched_image_defaults
VFKIT="${VFKIT:-vfkit}"
command -v "$VFKIT" >/dev/null || { echo "error: $VFKIT not found (brew install vfkit, or set VFKIT=)" >&2; exit 1; }
[ -f "$KERNEL" ] || { echo "error: missing kernel: $KERNEL $QEMU_HINT" >&2; exit 1; }
[ -f "$ROOTFS" ] || { echo "error: missing rootfs: $ROOTFS $QEMU_HINT" >&2; exit 1; }
[ -f "$(root_cmdline_file)" ] || { echo "error: missing $(root_cmdline_file)" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"; [ -z "${QEMU_SSH_DIR:-}" ] || rm -rf "$QEMU_SSH_DIR"' EXIT

append="$(cat "$(root_cmdline_file)") console=hvc0 init=$INIT"
append="$append systemd.set_credential=login.noauth:yes"

# vsock's "connect" mode: the host connects to a port the guest listens on, through the
# Unix socket vfkit makes at socketURL. Not "listen", which is vfkit's default and the
# other direction — guest to a socket the host listens on, as podman machine's ignition
# server does — and with which nothing on the host end ever answers ssh.
vsock=()
if [ "${SSH:-1}" = 1 ]; then
    SSH_PROXY="nc -U $work/ssh.sock"
    qemu_ssh_setup
    append="$append$QEMU_APPEND_EXTRA"
    vsock=(--device "virtio-vsock,port=22,socketURL=$work/ssh.sock,connect")
    echo ">> ssh: ./tools/ssh.sh  (over vsock, key in $SSH_DIR, gone when the VM exits)"
fi

qemu_var_disk
append="$append${QEMU_VAR_APPEND:-}"
var=()
[ -z "${VAR_DISK:-}" ] || var=(--device "virtio-blk,path=$(realpath "$VAR_DISK")")
share=()
[ -z "${SHARE:-}" ] || share=(--device "virtio-fs,sharedDir=$(realpath "$SHARE"),mountTag=share")

# The kernel arguments go in --kernel-cmdline, not in --bootloader linux,…,cmdline=: they
# carry commas and double quotes of their own (dm-mod.create="…"), which the
# comma-separated form would split on. The empty initrd is for vfkit versions that insist
# on one with these flags (verify); the kernel finds nothing in it and mounts the root
# from the command line as it does everywhere else. The root disk is attached first, so
# it is /dev/vda, which rootfs.cmdline names.
: > "$work/empty.initrd"
echo ">> booting $ROOTFS with $KERNEL in $("$VFKIT" --version 2>/dev/null | head -1)"
"$VFKIT" --cpus "${CPUS:-2}" --memory "${MEM:-1024}" \
    --kernel "$(realpath "$KERNEL")" --initrd "$work/empty.initrd" --kernel-cmdline "$append" \
    --device "virtio-blk,path=$(realpath "$ROOTFS")" \
    ${var[@]+"${var[@]}"} \
    --device virtio-net,nat \
    --device virtio-serial,stdio \
    --device virtio-rng \
    ${vsock[@]+"${vsock[@]}"} \
    ${share[@]+"${share[@]}"}
