#!/usr/bin/env bash
# A five-minute tour of the image, on a throwaway machine:
#
#     ./tools/demo.sh          # boot a fresh `demo` machine and walk through it
#     ./tools/demo.sh reset    # delete it again: the machine, its /var and its key
#
# Four stops, each a few commands run over ssh and shown as they run:
#
#   1. the immutable root: EROFS on dm-verity, read-only, the root hash on the kernel
#      command line; the only writable state is /var
#   2. its identity: os-release with the commit it was built from, the machine ID and
#      hostname a VM gives it, and fetch
#   3. a system extension attached to the running machine: the base has no podman, so
#      podman.raw is copied to /var/lib/extensions and merged with systemd-sysext
#   4. a container: a one-layer image made from the guest's own bash, run by podman and
#      crun, offline
#
# It needs what tools/vm.sh needs — qemu, ssh, and KVM to be quick — and an image with its
# extensions: this checkout's build (./tools/build-image.sh) if there is one, else CI's
# (./tools/fetch-image.sh). Nothing is added to the image; the demo is a machine of
# tools/vm.sh named `demo`, so every run starts from a fresh /var and leaves the image as
# it was. Each run resets first; `reset` alone just cleans up. The machine stays up
# afterwards for a look around (./tools/vm.sh ssh demo).
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

NAME=demo
STATE="${VM_STATE:-output/vms}"
vm() { ./tools/vm.sh "$@"; }

reset() {
    if [ -f "$STATE/$NAME/config" ]; then
        vm stop "$NAME" >/dev/null
        vm rm "$NAME" --yes >/dev/null
        echo ">> removed the '$NAME' machine"
    fi
}

case "${1:-}" in
    reset) reset; exit 0 ;;
    '') ;;
    -h|--help) sed -n '2,/^set -e/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    *) echo "usage: ./tools/demo.sh [reset]" >&2; exit 2 ;;
esac

# The image vm.sh would pick for a new machine, named explicitly so the extension comes
# from the same place.
if [ -f output/rootfs.erofs ] && [ -f output/rootfs.cmdline ] && [ -f rootfs/boot/bzImage ]; then
    image=local ext=output/extensions/podman.raw
else
    image=ci ext=output/boot-image/extensions/podman.raw
fi
[ -f "$ext" ] || {
    echo "error: no $ext; build the image (./tools/build-image.sh) or fetch CI's (./tools/fetch-image.sh)" >&2
    exit 1
}

reset
vm start "$NAME" --image "$image" --no-ext --size 2G --mem 2048 | grep -v '^   '

ssh_cfg="$STATE/$NAME/ssh/config"
# guest <command>: show it, then run it as root in the guest.
guest() {
    printf '\n\033[1m# %s\033[0m\n' "$1"
    ssh -F "$ssh_cfg" -l root kaetzli "$1" || true
}
stop() { printf '\n\033[1;34m== %s\033[0m\n' "$1"; }

stop "1. The root is read-only, and verified"
guest 'findmnt -no SOURCE,FSTYPE,OPTIONS /'
guest "sed -n 's/.* sha256 \([0-9a-f]*\) .*/dm-verity root hash: \1/p' /proc/cmdline"
guest 'touch /usr/demo'
guest 'findmnt -no SOURCE,FSTYPE /var   # state lives here: a disk of its own, or RAM'

stop "2. What it is, and which machine"
guest '. /etc/os-release; echo "$PRETTY_NAME, built from ${BUILD_ID:-an unrecorded commit}"'
guest 'echo "hostname $(</proc/sys/kernel/hostname), machine ID $(</etc/machine-id)"'
guest 'fetch'

stop "3. An extension, attached while it runs"
guest 'command -v podman || echo "no podman in the base image"'
printf '\n\033[1m# (host) scp %s kaetzli:/var/lib/extensions/\033[0m\n' "$ext"
ssh -F "$ssh_cfg" -l root kaetzli 'mkdir -p /var/lib/extensions'
scp -q -F "$ssh_cfg" "$ext" root@kaetzli:/var/lib/extensions/podman.raw
guest 'systemd-sysext refresh && systemd-sysext status'
guest 'podman --version'

stop "4. A container"
# bash, its libraries and the loader, from the guest's own read-only /usr.
guest 'rm -rf /run/img && mkdir -p /run/img/usr/bin /run/img/usr/lib &&
cp -L /usr/bin/bash /run/img/usr/bin/ && cp -L /usr/lib/libc.so.6 /usr/lib/libtinfo.so.6 /usr/lib/ld-linux-* /run/img/usr/lib/ &&
ln -s usr/bin /run/img/bin && ln -s usr/lib /run/img/lib && ln -s usr/lib /run/img/lib64 &&
tar -C /run/img -c . | podman import -q - localhost/demo'
guest "podman run --rm localhost/demo /usr/bin/bash -c 'echo \"hello from PID \$\$, in a container on \$(</etc/hostname)\"'"

cat <<EOF

Done. The machine is still running:
   ./tools/vm.sh ssh $NAME --root     look around
   ./tools/demo.sh reset            delete it; the next ./tools/demo.sh starts fresh
EOF
