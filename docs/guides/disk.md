# Bootable disk

`tools/build-disk.sh` turns an assembled image into a disk that UEFI firmware boots
directly: a local VMM, a hypervisor, or a cloud that boots uploaded disks. There is no
installer and no bootloader. A newer image means building a new disk and replacing the
VM.

```sh
./tools/build-image.sh                 # or put a release's rootfs.erofs and kaetzli.efi in a directory
./tools/build-disk.sh                  # output/kaetzli-disk-<arch>.raw and .qcow2
```

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

Accounts have no passwords. Log in with `--ssh-key`, or with a key the user data
installs.

The UKI on the disk is unsigned. To boot under Secure Boot, sign it with your own key
(`tools/sign-uki.sh`, [Secure Boot](secure-boot.md)). Under Secure Boot, `/etc` stays
read-only.

## Testing

`./test/disk.py [disk]` boots a disk under OVMF, with a metadata service and an SMBIOS
UUID. It checks whatever the disk's own command line asks for. Set `DISK_BUS=scsi` for a
disk built with `--bus scsi`.
