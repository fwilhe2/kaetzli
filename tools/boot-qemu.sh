#!/usr/bin/env bash
# Boot the fetched rootfs + kernel in QEMU (serial console in this terminal), once: by
# default nothing is kept, and the ssh key is gone with qemu. For a machine that keeps its
# /var and its key across reboots and image updates, see tools/vm.sh.
#
# Overrides:
#   ARCH     amd64 or arm64          (default the host's own architecture)
#   OUT      where the image is      (default output/boot-image, or output/boot-image-$ARCH
#                                    if fetch-image.sh was run with a non-default ARCH)
#   KERNEL   path to kernel image   (default $OUT/bzImage)
#   ROOTFS   path to EROFS image    (default $OUT/rootfs.erofs)
#   INIT     PID 1 to run           (default /usr/lib/systemd/systemd)
#   MEM      RAM in MB              (default 1024)
#   CPUS     vCPUs                  (default 2)
#   SSH      0 to boot without the ssh forward and key below   (default 1)
#   SSH_PORT host port forwarded to the guest's 22   (default the first free one from 2222)
#   SSH_DIR  where the ephemeral key and ssh_config go   (default output/ssh)
#   VAR_DISK a file to keep this VM's /var on, created if missing; without it /var is RAM
#            and every boot starts empty. VAR_SIZE sizes a new one (default 4G). See
#            qemu_var_disk in test/qemu-lib.sh. e.g. VAR_DISK=output/var.img
#   SYSEXTS  system extensions to attach as read-only disks, a space-separated list, e.g.
#            SYSEXTS=kubernetes or SYSEXTS="podman debug":
#            EXTENSIONS/extensions/<name>.raw (and confexts/<name>.raw), merged at boot.
#            EXTENSIONS defaults to the image's directory; qemu_sysexts in test/qemu-lib.sh
#   QEMU_PROFILE  pc (default), microvm (Firecracker's devices; on amd64 KERNEL has to be
#            an uncompressed vmlinux) or vz (Virtualization.framework's: hvc0, and
#            VIRTIOFS_SOCKET/VSOCK_CID for virtiofs and vsock). See qemu_setup in
#            test/qemu-lib.sh and test/vmm.py. vz has no qemu monitor on the console, so
#            Ctrl-a x does not exit it: `poweroff` in the guest, or kill qemu.
#
# The guest gets one virtio-net NIC on qemu's user-mode network (10.0.2.15/24, gateway
# and DNS forwarder at 10.0.2.2/10.0.2.3), which systemd-networkd picks up over DHCP.
# It is unprivileged and outbound-only apart from one forward, 127.0.0.1:$SSH_PORT to
# the guest's sshd.
#
# Log in over ssh, without a password, from another terminal:
#
#     ./tools/ssh.sh                  # a shell as `user`
#     ./tools/ssh.sh systemctl status # or a command
#     ./tools/ssh.sh --root           # a root shell
#     scp -F output/ssh/config file kaetzli:/tmp/
#
# Every boot gets a fresh key pair, handed to the guest as a credential and deleted when
# qemu exits (qemu_ssh_setup in test/qemu-lib.sh). At the console, a username is enough.
# The guest is assembled by test/qemu-lib.sh, the same code as the boot tests.
#
# Drop to a raw shell instead of systemd (handy for debugging a broken boot):
#   INIT=/bin/bash ./tools/boot-qemu.sh
#
# Exit the guest with Ctrl-a then x.
set -euo pipefail

cd "$(dirname "$0")/.."
source test/qemu-lib.sh

qemu_setup

fetched_image_defaults

qemu_preflight
if [ "${SSH:-1}" = 1 ]; then
    qemu_ssh_setup
    echo ">> ssh: ./tools/ssh.sh  (127.0.0.1:$SSH_PORT, key in $SSH_DIR, gone when qemu exits)"
fi
qemu_argv

# Interactive, so the console is this terminal rather than a fifo and a log: no
# qemu_boot, nothing to drive. Extra qemu flags are passed through. Not exec'd, so the
# ephemeral key can be deleted once the guest is gone.
trap '[ -z "${QEMU_SSH_DIR:-}" ] || rm -rf "$QEMU_SSH_DIR"' EXIT
"$QEMU" "${QEMU_ARGV[@]}" "$@"
