# Kaetzli as a cloud VPS's own system

**Status: proposal, nothing implemented.** The provider notes are from the providers'
documentation and community reports as of November 2026; no provider has booted kaetzli
yet. Firmware modes in particular change (Hetzner moved new x86 types to UEFI in 2023), so
a test boot on the chosen provider settles each row.

Running kaetzli *inside* a VPS works today: install qemu on any Linux VPS and use the
launcher or `tools/vm.sh` (slow without `/dev/kvm`, which many VPS types do not offer).
This proposal is about the other way: kaetzli as the operating system the provider boots.

## What exists, and what a provider needs

Today an image is a root filesystem (`rootfs.erofs`, verity appended), a kernel and a
command line, which a VMM starts directly (`-kernel`). The UKI (`kaetzli.efi`, both
arches) already bundles the kernel, that command line and systemd's EFI stub, so UEFI
firmware can start it with no bootloader. A provider instead boots *a disk* through its
firmware, and starts it without anything kaetzli reads today.

| Need | Today | What it would take |
| --- | --- | --- |
| A bootable disk | None: no partition table, ESP or bootloader ([image variants](image-variants.md) leaves a self-bootable disk platform open) | A host tool writing a raw GPT disk: an ESP with the UKI as `EFI/BOOT/BOOTX64.EFI` (`BOOTAA64.EFI` on arm64), and the EROFS+verity root as a partition. Output raw, qcow2 or `disk.raw` in a `.tar.gz` as the provider wants. |
| Firmware | The UKI is UEFI only | UEFI providers need nothing more. A BIOS-only provider needs a bootloader on the disk too; this proposal leaves BIOS out. |
| The root device | The command line names `/dev/vda` (`image/build-rootfs.sh`: "the one thing a consumer may have to rewrite") | The partition, under the provider's disk name: `/dev/vda2` on virtio-blk, `/dev/sda2` on virtio-scsi (Hetzner). The launcher's Go code already rewrites a UKI's command line (`go/internal/uki`); the tool would reuse it. |
| Drivers | virtio-blk, virtio-scsi (`sd`), virtio-net, the serial console, VGA text console | Enough for KVM providers. NVMe, ENA, gVNIC and Hyper-V are absent, and adding them goes against constraint 2 (VMs, and only what a VM needs); a decision of its own if ever. |
| Login | No passwords; ssh keys only by systemd credential, over qemu's fw_cfg | Providers pass no credentials, and cloud-init is Python (constraint 5). Either the key goes into the UKI's command line as `systemd.set_credential=ssh.authorized_keys.root:…` when the disk is built (one key per image; not secret, and not under the Secure Boot model there anyway), or a small bash + curl unit reads the provider's metadata service. |
| Network | networkd, DHCP on `en*`/`eth*` | Fine wherever the provider runs DHCP, which every provider below does for IPv4. IPv6 is often static, from metadata: the same unit, if wanted. |
| Console | Kernel and getty on the serial port | Web consoles are often VGA: `console=tty0` as well, on the disk's command line. Google Cloud and some others offer the serial port. |
| State | `/var` in RAM, lost at shutdown | A third partition, or an attached volume, by `systemd.mount-extra=` on the command line. |
| Updates | No package manager; snapshots replace each other | Rebuild the disk from a new snapshot and replace the server. Two root partitions with a switch is later work ([always latest](always-latest.md)). |
| Secure Boot | The launcher enrolls the UKI's digest in its own OVMF variables | No provider lets you enroll your own db, so the firmware does not check the UKI. The root is still held to the verity hash, but nothing holds the UKI that carries it. |

## Providers

What decides fitness: KVM with virtio devices, uploading a whole disk image (not an ISO,
not a root filesystem), UEFI boot, DHCP, no required agent.

### Good fit

| Provider | Why | Catches |
| --- | --- | --- |
| **Exoscale** | qcow2/raw templates registered from a URL; the boot mode is a flag, `--boot-mode uefi` | Legacy BIOS is the default, so the flag is required; qcow2 with preallocation off |
| **Hetzner Cloud**, CPX, CCX, CAX | New x86 types boot UEFI by default; Arm (CAX) is always UEFI | No upload API: rescue system, `dd` to the disk, snapshot (`hcloud-upload-image` automates it). The disk is `/dev/sda` (virtio-scsi). Cost-optimized CX types were reported BIOS-only in late 2025. A server's disk must be at least the snapshot's. |
| **Scaleway** | Requires UEFI boot from a full disk image; qcow2 imported from Object Storage | Imported as a snapshot first, in the bucket's region and project |
| **OpenStack clouds** (OVHcloud Public Cloud and others) | Raw upload to Glance, virtio-scsi as an image property; UEFI by `hw_firmware_type=uefi` | Whether OVHcloud honours the UEFI property is not in its documentation |
| **Google Cloud**, older series (E2, N1, N2) | VirtIO-SCSI and VirtIO-Net by default; `UEFI_COMPATIBLE` as an image flag; a serial console, which helps a first boot | Third generation and later (C3, N4, …) take only NVMe and gVNIC. The upload is `disk.raw` in an oldgnu `.tar.gz`, a whole number of GB. |

### Poor fit

| Provider | Why not |
| --- | --- |
| **DigitalOcean** | Custom images must boot BIOS, and must carry cloud-init (or another listed agent). DHCP itself works. |
| **Linode (Akamai)** | Uploads must be ext3/ext4; the way around is rescue mode, `dd` to a raw disk and "Direct Disk" boot, BIOS-style. |
| **Vultr** | Custom systems come as ISOs, and kaetzli has no installer ISO; the firmware mode of cloud VPS is undocumented. |
| **AWS, Azure** | NVMe and ENA (AWS), Hyper-V (Azure): drivers the kernel does not have (constraint 2). |

## Order

1. **A disk-image tool** (host side: Python, or the launcher's Go for `go/internal/uki`),
   from a snapshot or a local build: GPT, ESP with the UKI, the root partition, the
   command line rewritten for the disk name, `console=tty0`, the ssh key as a credential,
   optionally a `/var` partition. Its output boots under qemu with OVMF first, so CI can
   test it without a provider.
2. **One provider by hand**: Hetzner Cloud on CPX, or Exoscale (the UEFI flag makes the
   firmware explicit, without the snapshot detour). Record what boots and what the
   console shows.
3. **Only then** a metadata unit (ssh keys and IPv6 from the provider), if a key baked into
   the image proves too rigid.

## Open decisions

- Is a cloud disk a published artifact (an image per snapshot and arch) or something users
  build from a snapshot? Building keeps provider details out of the release.
- The ssh key in the command line is per image; per server needs the metadata unit, which
  is per provider.
- A metadata unit is new image code (bash + curl, under constraint 5); which providers'
  endpoints, and whether it belongs in an extension rather than the base.
- BIOS support (a bootloader on the disk) would open DigitalOcean, Linode and Hetzner CX,
  at the cost of a component the image does not have.

## Sources

- DigitalOcean: [custom images limits](https://docs.digitalocean.com/products/custom-images/details/limits/), [features](https://docs.digitalocean.com/products/custom-images/details/features/)
- Hetzner: [hcloud-upload-image](https://github.com/apricote/hcloud-upload-image), [Talos on Hetzner](https://docs.siderolabs.com/talos/v1.7/platform-specific-installations/cloud-platforms/hetzner), [new x86 VMs UEFI-only (srvos)](https://github.com/nix-community/srvos/issues/732), [snapshot UEFI fix](https://dol.ch/blog/hetzner-snapshot-wont-boot-uefi-fix), [Kairos cloud images](https://github.com/mudler-agent/kairos-cloud-images)
- Exoscale: [instance-template register](https://community.exoscale.com/reference/cli/exo/compute/instance-template/register/), [custom templates](https://community.exoscale.com/product/compute/instances/how-to/custom-templates/)
- Scaleway: [OpenWrt image tutorial](https://www.scaleway.com/en/docs/tutorials/create-openwrt-image-for-scaleway/), [snapshot import](https://www.scaleway.com/en/docs/instances/how-to/snapshot-import-export-feature/)
- OVHcloud: [upload your own image](https://docs.ovhcloud.com/en/guides/public-cloud/compute/upload-own-image)
- Google Cloud: [manual image import](https://docs.cloud.google.com/compute/docs/import/import-existing-image), [machine families](https://docs.cloud.google.com/compute/docs/machine-resource), [migrate to a new VM](https://docs.cloud.google.com/compute/docs/import/migrate-to-new-vm)
- Linode: [upload an image](https://techdocs.akamai.com/cloud-computing/docs/upload-an-image), [custom distribution](https://www.linode.com/docs/guides/install-a-custom-distribution/)
- Vultr: [upload ISO](https://www.vultr.com/features/upload-iso/)
