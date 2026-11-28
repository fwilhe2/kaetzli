# Bootable disk

The image comes two ways. `rootfs.erofs` with a separate kernel and command line suits a
VMM that is handed the kernel (qemu's `-kernel`, Firecracker, vfkit): `tools/vm.sh`, the
launcher and the tests boot it so. A disk image suits anything that boots disks through
UEFI firmware: a hypervisor, a cloud that takes uploaded disks, qemu with OVMF. Both hold
the same root, byte for byte. There is no installer and no bootloader. A newer image means
a new disk and replacing the VM.

Each snapshot release has the disk as `disk-<arch>.qcow2`, with the default options below:

```sh
./tools/fetch-image.sh --disk          # output/disk-image/disk.qcow2, verified
```

Or build it. `tools/build-image.sh` makes the default disk after the erofs image
(`output/kaetzli-disk-<arch>.qcow2`); `tools/build-disk.sh` makes one with other options,
from that build or from a directory holding a release's `rootfs.erofs` and `kaetzli.efi`:

```sh
./tools/build-disk.sh                  # output/kaetzli-disk-<arch>.raw and .qcow2
./tools/build-disk.sh --cloud openstack     # runs the instance's user data at boot
./tools/build-disk.sh --bus scsi --ssh-key ~/.ssh/id_ed25519.pub
```

Convert to another format with `qemu-img convert` (`-O vmdk`, `-O vpc` for VHD).

## Layout

| Partition | Contents |
| --- | --- |
| 1, ESP (64 MiB) | The UKI as `EFI/BOOT/BOOTX64.EFI` (`BOOTAA64.EFI` on arm64) |
| 2, root | `rootfs.erofs` with its dm-verity hash tree |
| 3, `/var` (20 GiB) | Blank; formatted ext4 at first boot and kept across reboots |

The UKI's command line is set for this disk:
- the verity root on partition 2;
- `/var` on partition 3;
- a VGA console (`tty0`) as well as the serial port;
- `systemd.machine_id=firmware`, so the machine ID is the SMBIOS UUID that a cloud sets
  per instance.

`/home`, `/root`, `/opt/bin` and the writable layer of `/etc` live in `/var`, so they
persist too.

Both files are sparse and reproducible: the same image and options give the same bytes.

## Options

| Option | Effect |
| --- | --- |
| `--bus virtio\|scsi` | Device names for virtio-blk (`/dev/vda`, default) or virtio-scsi (`/dev/sda`). Must match how the VM attaches the disk. |
| `--var-size SIZE` | Size of the `/var` partition. `0` leaves it out: `/var` is RAM, as with `rootfs.erofs`. |
| `--cloud openstack` | Run the instance's [user data](user-data.md) from the metadata service. |
| `--ssh-key FILE` | Let root log in with this public key. For providers without a metadata service; the key is on the kernel command line, which is readable in the guest. |
| `--append ARGS` | More kernel arguments. |
| `--name NAME`, `--image DIR`, `--out DIR` | Output name, input directory (default `output/`), output directory. |
| `--no-raw` | Keep only the qcow2. |

The VM's disk must be at least the image's virtual size. The `/var` partition does not
grow into a larger disk, because the image has no `systemd-repart`.

## Booting it

QEMU with OVMF:

```sh
qemu-system-x86_64 -enable-kvm -cpu host -machine q35 -m 1024 \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
    -drive if=pflash,format=raw,file=ovmf-vars.fd \
    -drive file=output/kaetzli-disk-amd64.qcow2,if=virtio -nographic
```

`ovmf-vars.fd` is a copy of `/usr/share/OVMF/OVMF_VARS_4M.fd`. The firmware writes to it,
so give each VM its own copy.

OpenStack (Glance):

```sh
./tools/build-disk.sh --cloud openstack
openstack image create kaetzli --disk-format qcow2 --file output/kaetzli-openstack-amd64.qcow2 \
    --property hw_firmware_type=uefi --property hw_disk_bus=virtio --property hw_machine_type=q35
```

Accounts have no passwords, so a released disk takes the console only, through the
`login.noauth` credential (qemu's `-fw_cfg name=opt/io.systemd.credentials/login.noauth,string=yes`).
For ssh, build one with `--ssh-key`, or use a key the user data installs (`--cloud`).

The UKI on the disk is unsigned. To boot under Secure Boot, sign it with your own key
(`tools/sign-uki.sh`, [Secure Boot](secure-boot.md)). Under Secure Boot, `/etc` stays
read-only.

## Testing

`./test/disk.py [disk]` boots a disk under OVMF, with a metadata service and an SMBIOS
UUID. It checks whatever the disk's own command line asks for. Set `DISK_BUS=scsi` for a
disk built with `--bus scsi`.
