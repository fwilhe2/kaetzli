#!/usr/bin/env bash
# A bootable disk, for any VMM or cloud that boots disks through UEFI firmware, made from
# an assembled image:
#
#     ./tools/build-disk.sh [options]
#
#     --cloud openstack     a cloud's preset: its user-data source (packages/user-data) and
#                           the name kaetzli-openstack-<arch>
#     --bus virtio|scsi     the disk the VM sees: /dev/vda (virtio-blk, the default) or
#                           /dev/sda (virtio-scsi, as some providers attach it)
#     --var-size SIZE       the /var partition (default 20G); 0 for none, /var in RAM
#     --ssh-key FILE        a public key root may log in with, on the command line as the
#                           ssh.authorized_keys.root credential, as the VM tools pass one;
#                           for a provider that has no metadata service to ask
#     --append ARGS         more kernel arguments
#     --name NAME           output base name (default kaetzli-disk-<arch>)
#     --no-raw              keep only the qcow2 (tools/build-image.sh's, CI's)
#     --image DIR           where rootfs.erofs and kaetzli.efi are (default output/)
#     --out DIR             default output/
#
# Reads rootfs.erofs and kaetzli.efi (tools/build-image.sh, or a release), unsigned. Writes
# DIR/NAME.raw (sparse) and DIR/NAME.qcow2, both made in the pinned assembler image, so
# the bytes do not depend on the host's qemu-img. tools/build-image.sh makes the default
# disk after the erofs image; releases publish its qcow2 as disk-<arch>.qcow2. Nothing is
# installed or configured afterwards: the disk is the image, and a newer image is a new
# disk. docs/guides/disk.md has the uploads.
#
# GPT, three partitions:
#
#   1  ESP, 64 MiB   the UKI as EFI/BOOT/BOOTX64.EFI (BOOTAA64.EFI on arm64): no bootloader,
#                    the firmware's removable-media path starts it
#   2  root          rootfs.erofs as it is, dm-verity's hash tree appended
#   3  /var          blank, formatted ext4 at first boot (x-systemd.makefs) and kept
#
# The UKI's command line is the image's with these changes: the verity table names
# partition 2 of the bus's disk instead of /dev/vda; console=tty0 before the serial
# console, for a provider's VGA web console; /var on partition 3 (systemd.mount-extra=);
# systemd.machine_id=firmware, the SMBIOS UUID, which a cloud sets per instance, so the
# journal and a node's identity survive a reboot (a VM without one gets a new ID per boot,
# as before); and whatever --cloud, --ssh-key and --append add. The rewrite is the
# launcher's code (kaetzli uki-cmdline), run in the golang container go/go.mod names; the
# disk is made in the pinned assembler image.
#
# /var is a fixed size: the image has no systemd-repart to grow a partition
# (packages/systemd/build.sh), so the VM's disk must be at least the image's virtual size,
# and the rest of a larger one is unused.
#
# Reproducible: partition and disk GUIDs and the ESP's serial are derived from the root
# hash and the options, and its files get KAETZLI_SOURCE_DATE_EPOCH.
set -euo pipefail
cd "$(dirname "$0")/.."
source tools/lib.sh

usage() {
    echo "usage: $0 [--cloud openstack] [--bus virtio|scsi] [--var-size SIZE|0] [--ssh-key FILE]" >&2
    echo "          [--append ARGS] [--name NAME] [--image DIR] [--out DIR] [--no-raw]" >&2
    exit 2
}
cloud="" bus=virtio var_size=20G ssh_key="" append="" name="" image=output out=output raw=1
while [ $# -gt 0 ]; do
    if [ "$1" = --no-raw ]; then raw=0; shift; continue; fi
    [ $# -ge 2 ] || usage
    case "$1" in
        --cloud) cloud=$2 ;;
        --bus) bus=$2 ;;
        --var-size) var_size=$2 ;;
        --ssh-key) ssh_key=$2 ;;
        --append) append=$2 ;;
        --name) name=$2 ;;
        --image) image=$2 ;;
        --out) out=$2 ;;
        *) usage ;;
    esac
    shift 2
done

arch=$(normalize_arch)
case $arch in
    amd64) efi_name=BOOTX64.EFI ;;
    arm64) efi_name=BOOTAA64.EFI ;;
esac
case $bus in
    virtio) dev=/dev/vda ;;
    scsi) dev=/dev/sda ;;
    *) echo "error: --bus is virtio or scsi, not '$bus'" >&2; exit 2 ;;
esac
cloud_args=""
case $cloud in
    "") ;;
    openstack) cloud_args="kaetzli.user-data=openstack" ;;
    *) echo "error: no preset for --cloud '$cloud' (known: openstack)" >&2; exit 2 ;;
esac
name=${name:-kaetzli-${cloud:-disk}-$arch}
case $name in
    *[!A-Za-z0-9._-]*|"") echo "error: --name '$name': letters, digits, . _ - only" >&2; exit 2 ;;
esac
if [ "$var_size" = 0 ]; then
    var_bytes=0
else
    var_bytes=$(numfmt --from=iec "$var_size")
fi
if [ -n "$ssh_key" ]; then
    if ! { [ -f "$ssh_key" ] && grep -qE '^(ssh-|ecdsa-|sk-)' "$ssh_key"; }; then
        echo "error: --ssh-key $ssh_key is not an OpenSSH public key file" >&2; exit 2
    fi
fi

rootfs=$image/rootfs.erofs uki=$image/kaetzli.efi
for f in "$rootfs" "$uki"; do
    [ -f "$f" ] || { echo "error: no $f (tools/build-image.sh assembles it)" >&2; exit 1; }
done

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$out"

go_version=$(sed -n 's/^toolchain go//p' go/go.mod)
kaetzli_go() {
    podman run --rm --network=none --security-opt label=disable \
        -v "$PWD/go:/src:ro" -w /src -e GOTOOLCHAIN=local -e GOCACHE=/tmp/gocache -e GOFLAGS=-buildvcs=false \
        -v "$(realpath "$uki"):/in/kaetzli.efi:ro" -v "$tmp:/out" \
        "docker.io/library/golang:$go_version" go run ./cmd/kaetzli uki-cmdline --uki /in/kaetzli.efi "$@"
}

image_cmdline=$(kaetzli_go)
case $image_cmdline in
    *dm-mod.create=*/dev/vda*) ;;
    *) echo "error: $uki's command line has no verity table on /dev/vda: $image_cmdline" >&2; exit 1 ;;
esac
# Every /dev/vda is the root's (waitfor, and the table's data and hash devices); the
# console= the image has (the serial port) stays last, so it is /dev/console.
cmdline=$(sed -E "s#/dev/vda([ \"]|\$)#${dev}2\\1#g; s# console=# console=tty0 console=#" <<< "$image_cmdline")
[ "$var_bytes" = 0 ] || cmdline+=" systemd.mount-extra=${dev}3:/var:ext4:x-systemd.makefs,x-systemd.growfs"
cmdline+=" systemd.machine_id=firmware"
[ -z "$cloud_args" ] || cmdline+=" $cloud_args"
[ -z "$ssh_key" ] || cmdline+=" systemd.set_credential_binary=ssh.authorized_keys.root:$(base64 -w0 < "$ssh_key")"
[ -z "$append" ] || cmdline+=" $append"
echo ">> command line: $cmdline"
kaetzli_go --set "$cmdline" --out "/out/$efi_name"

# GUIDs from the root hash and the command line, so the same image and options make the
# same disk, and two disks made differently do not share them.
seed=$(printf '%s\n%s' "$image_cmdline" "$cmdline" | sha256sum | cut -c1-64)
guid() {
    local h
    h=$(printf 'kaetzli-disk %s %s' "$1" "$seed" | sha256sum | cut -c1-32)
    # Version 4 and RFC 4122 variant bits, as a random one would have.
    printf '%s-%s-4%s-%x%s-%s\n' "${h:0:8}" "${h:8:4}" "${h:13:3}" \
        $(( (16#${h:16:1} & 3) | 8 )) "${h:17:3}" "${h:20:12}"
}

mib=$((1024 * 1024))
esp_mib=64
root_mib=$(( ($(stat -c %s "$rootfs") + mib - 1) / mib ))
var_mib=$(( (var_bytes + mib - 1) / mib ))
# 1 MiB before the first partition, 1 MiB after the last for the backup GPT.
total=$(( (1 + esp_mib + root_mib + var_mib + 1) * mib ))
sectors() { echo $(( $1 * mib / 512 )); }

{
    echo "label: gpt"
    echo "label-id: $(guid disk)"
    echo "unit: sectors"
    echo "first-lba: 2048"
    echo "start=2048, size=$(sectors $esp_mib), type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, uuid=$(guid esp), name=\"esp\""
    echo "size=$(sectors "$root_mib"), type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, uuid=$(guid root), name=\"root\""
    [ "$var_mib" = 0 ] ||
        echo "size=$(sectors "$var_mib"), type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, uuid=$(guid var), name=\"var\""
} > "$tmp/layout.sfdisk"
serial=$(guid esp | tr -d - | cut -c1-8)

disk=$name.raw
assembler=$(./tools/prep.sh assembler)
podman run --rm --network=none --security-opt label=disable \
    -e SOURCE_DATE_EPOCH="$KAETZLI_SOURCE_DATE_EPOCH" \
    -v "$(realpath "$rootfs"):/in/rootfs.erofs:ro" -v "$tmp:/work" -w /work \
    "$assembler" bash -euc "
        truncate -s $total $disk
        sfdisk --quiet --no-reread --no-tell-kernel $disk < layout.sfdisk
        mkfs.fat --invariant -i $serial -n KAETZLI -C esp.img $((esp_mib * 1024)) > /dev/null
        touch -d @\$SOURCE_DATE_EPOCH $efi_name
        mmd -i esp.img ::/EFI ::/EFI/BOOT
        mcopy -m -i esp.img $efi_name ::/EFI/BOOT/$efi_name
        dd if=esp.img of=$disk bs=1M seek=1 conv=notrunc,sparse status=none
        dd if=/in/rootfs.erofs of=$disk bs=1M seek=$((1 + esp_mib)) conv=notrunc,sparse status=none
        sfdisk --dump $disk | sed -n 's/^$disk/  /p'
        qemu-img convert -f raw -O qcow2 $disk $name.qcow2
    "
mv "$tmp/$name.qcow2" "$out/$name.qcow2"
echo ">> $out/$name.qcow2 ($(du -h "$out/$name.qcow2" | cut -f1))"
if [ "$raw" = 1 ]; then
    mv "$tmp/$disk" "$out/$disk"
    echo ">> $out/$disk ($(du -h --apparent-size "$out/$disk" | cut -f1) virtual, $(du -h "$out/$disk" | cut -f1) on disk)"
fi
