#!/usr/bin/env bash
# Boot the image in Firecracker (Linux host with KVM), serial console in this terminal.
#
#     ./tools/boot-firecracker.sh
#     ./tools/ssh.sh                      # from another terminal, over vsock
#
# Overrides, as for tools/boot-qemu.sh:
#   ARCH, OUT, KERNEL, ROOTFS, INIT, MEM, CPUS, SSH, SSH_DIR, VAR_DISK, VAR_SIZE
#   FIRECRACKER   the binary                       (default: firecracker on PATH)
#   TAP           a tap device to give the guest a network (default: none, see below)
#   GUEST_IP, HOST_IP, DNS   its addressing        (default 172.16.0.2/24, 172.16.0.1, 1.1.1.1)
#
# Firecracker gets a release binary from github.com/firecracker-microvm/firecracker; it
# needs /dev/kvm and nothing else, not root.
#
# What it boots is the image every other script boots, unchanged: rootfs.erofs attached
# read-only, and the kernel arguments from rootfs.cmdline that carry its dm-verity root
# hash. The kernel is the one CI built, too. On amd64 Firecracker does not load a bzImage —
# it boots an uncompressed vmlinux through its PVH entry point — so the vmlinux inside the
# bzImage is extracted at every boot (extract_vmlinux, tools/lib.sh); arm64's Image is
# already uncompressed. The devices are virtio over MMIO, which packages/kernel/build.sh's
# guest.config builds in and test/vmm.py checks on qemu's equivalent machine.
#
# ssh needs no network: the guest has a vsock device, systemd's ssh generator puts sshd on
# vsock port 22 by itself, and Firecracker exposes the guest's vsock ports to the host as
# a Unix socket that takes one "CONNECT <port>" line first. qemu_ssh_setup's SSH_PROXY is
# that handshake, through socat, so ./tools/ssh.sh works exactly as it does for qemu.
#
# The network is opt-in, because Firecracker has no user-mode network: the guest gets a
# tap device on the host, and making one needs root. With TAP set, the guest is given a
# static address — there is no DHCP server on a tap — as a networkd credential matching
# its MAC, which outranks the image's DHCP-on-everything 20-wired.network for that one
# interface. A host side that routes it to the world, once:
#
#     sudo ip tuntap add dev tap0 mode tap user "$USER"
#     sudo ip addr add 172.16.0.1/24 dev tap0 && sudo ip link set tap0 up
#     sudo sysctl -w net.ipv4.ip_forward=1
#     sudo nft add table ip kaetzli
#     sudo nft add chain ip kaetzli post '{ type nat hook postrouting priority 100; }'
#     sudo nft add rule ip kaetzli post ip saddr 172.16.0.0/24 masquerade
#
# Leave the guest with `reboot` (reboot=k makes that end Firecracker), or kill it.
set -euo pipefail

cd "$(dirname "$0")/.."
source test/qemu-lib.sh

ARCH=$(normalize_arch "${ARCH:-}")
[ "$ARCH" = "$(normalize_arch)" ] ||
    { echo "error: Firecracker only runs guests of the host's own architecture" >&2; exit 1; }
fetched_image_defaults
FIRECRACKER="${FIRECRACKER:-firecracker}"
command -v "$FIRECRACKER" >/dev/null || {
    echo "error: $FIRECRACKER not found; get a release from" >&2
    echo "       https://github.com/firecracker-microvm/firecracker/releases (or set FIRECRACKER=)" >&2
    exit 1
}
[ -r /dev/kvm ] && [ -w /dev/kvm ] || { echo "error: Firecracker needs read-write /dev/kvm" >&2; exit 1; }
[ -f "$KERNEL" ] || { echo "error: missing kernel: $KERNEL $QEMU_HINT" >&2; exit 1; }
[ -f "$ROOTFS" ] || { echo "error: missing rootfs: $ROOTFS $QEMU_HINT" >&2; exit 1; }
[ -f "$(root_cmdline_file)" ] || { echo "error: missing $(root_cmdline_file)" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"; [ -z "${QEMU_SSH_DIR:-}" ] || rm -rf "$QEMU_SSH_DIR"' EXIT

kernel=$KERNEL
if [ "$ARCH" = amd64 ]; then
    kernel="$work/vmlinux"
    extract_vmlinux "$KERNEL" "$kernel"
fi

# Firecracker's console is an 8250 on both architectures (on arm64 a device-tree one), so
# ttyS0 either way. reboot=k because the guest has no ACPI to power off through: a reboot
# reaches Firecracker's i8042 and ends the VMM, which is the clean way out.
append="$(cat "$(root_cmdline_file)") console=ttyS0 reboot=k panic=1 init=$INIT"
append+=" systemd.set_credential=login.noauth:yes"

vsock="$work/vsock"
if [ "${SSH:-1}" = 1 ]; then
    command -v socat >/dev/null || { echo "error: ssh over vsock needs socat (or SSH=0)" >&2; exit 1; }
    SSH_PROXY="sh -c '{ printf \"CONNECT 22\\n\"; cat; } | socat - UNIX-CONNECT:$vsock'"
    qemu_ssh_setup
    append+="$QEMU_APPEND_EXTRA"
    echo ">> ssh: ./tools/ssh.sh  (over vsock, key in $SSH_DIR, gone when Firecracker exits)"
fi

qemu_var_disk
append+="${QEMU_VAR_APPEND:-}"
drives='{ "drive_id": "root", "path_on_host": "'"$(realpath "$ROOTFS")"'", "is_root_device": false, "is_read_only": true }'
[ -z "${VAR_DISK:-}" ] ||
    drives+=', { "drive_id": "var", "path_on_host": "'"$(realpath "$VAR_DISK")"'", "is_root_device": false, "is_read_only": false }'

net=
if [ -n "${TAP:-}" ]; then
    mac=06:00:ac:10:00:02
    network="[Match]
MACAddress=$mac

[Network]
Address=${GUEST_IP:-172.16.0.2/24}
Gateway=${HOST_IP:-172.16.0.1}
DNS=${DNS:-1.1.1.1}"
    append+=" systemd.set_credential_binary=network.network.10-firecracker:$(printf '%s\n' "$network" | base64 | tr -d '\n')"
    net='"network-interfaces": [ { "iface_id": "eth0", "host_dev_name": "'"$TAP"'", "guest_mac": "'"$mac"'" } ],'
fi

# A drive's root= is not Firecracker's to write (is_root_device false): the root is the
# dm-verity device rootfs.cmdline describes on top of /dev/vda, the first drive here.
cat > "$work/vm.json" <<EOF
{
  "boot-source": { "kernel_image_path": "$kernel", "boot_args": $(printf '%s' "$append" | sed 's/\\/\\\\/g; s/"/\\"/g; s/^/"/; s/$/"/') },
  "drives": [ $drives ],
  $net
  "vsock": { "guest_cid": 3, "uds_path": "$vsock" },
  "entropy": {},
  "machine-config": { "vcpu_count": ${CPUS:-2}, "mem_size_mib": ${MEM:-1024} }
}
EOF

echo ">> booting $ROOTFS with $KERNEL in $("$FIRECRACKER" --version | head -1)"
"$FIRECRACKER" --no-api --config-file "$work/vm.json"
