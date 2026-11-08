# Secure Boot

**Status: implemented for amd64 under qemu (OVMF), with a persistent `/var`; you sign the
UKI, and the launcher does it for you (`kaetzli start --secure-boot`).** Not yet: system
extensions in this mode, arm64, and VMMs that boot without
firmware (Firecracker, Virtualization.framework).

## Why

dm-verity makes the root read only what its root hash describes, and the root hash is on
the kernel command line. Whoever supplies the command line chooses the hash, so verity
alone proves the root matches *a* hash, not *ours*. The kernel cannot check a signature on
the hash here: dm-verity reads one only from a keyring that userspace fills, and the root
is set up by `dm-mod.create=` before there is any userspace (and there is no initrd).

A **unified kernel image** closes that: the kernel, the command line with the root hash,
and systemd's EFI stub in one PE binary, signed. Under Secure Boot the firmware runs only
an image signed by an enrolled key, and the stub then ignores any command line the VMM
passes. A different root hash means a different UKI, which the signature does not cover.

## With the launcher

```sh
kaetzli start --secure-boot --no-ext    # remembered; --no-secure-boot turns it off
```

- The first time, it makes **your key**: `secureboot/db.key` and `db.crt` in the
  launcher's data directory (beside `machines/`). Keep `db.key` private.
- Each start signs the snapshot's UKI, verified against `SHA256SUMS` when it was fetched,
  with that key (`go/internal/authenticode`). It then writes the machine's firmware
  variables fresh from the template of the host's OVMF, found through qemu's firmware
  descriptors (`go/internal/firmware`): your certificate in db, and a PK and KEK made for
  the machine and thrown away (`go/internal/efivars`). Nothing but qemu and OVMF on the
  host (Debian, Ubuntu: `ovmf`; Fedora: `edk2-ovmf`).
- The machine's name is its hostname by the `system.hostname` credential, since
  `systemd.hostname=` would be on the sealed command line. It takes because the image
  ships no `/etc/hostname`, which would outrank it; `DEFAULT_HOSTNAME=kaetzli` in
  os-release is the name when nothing gives one (`test/systemd.py` checks the credential
  takes).

## Build, sign, boot

```sh
./tools/build-image.sh                 # also writes output/kaetzli.efi, unsigned
./tools/sign-uki.sh                    # a throwaway key; or --key K.pem --cert C.pem
SECUREBOOT=output/secureboot ./tools/boot-qemu.py   # any kaetzli.qemu boot
./test/secureboot.py                   # what CI checks
```

Or without building: every snapshot release has `uki-<arch>.efi`, unsigned, and
`tools/fetch-image.sh` puts it beside the image as `output/boot-image/kaetzli.efi`:

```sh
./tools/fetch-image.sh
./tools/sign-uki.sh --key K.pem --cert C.pem output/boot-image/kaetzli.efi
SECUREBOOT=output/secureboot ./tools/boot-qemu.py
```

- `image/build-rootfs.sh` makes `output/kaetzli.efi` with `ukify` (the assembler image),
  from the kernel, `rootfs.cmdline` plus the console, os-release, and **our** systemd's
  stub (`packages/systemd`, `-Dbootloader=enabled`; the stub is removed from the image
  itself, as is everything else that option installs). Unsigned, it is byte-reproducible
  and checked by the reproducible job like the image.
- `tools/sign-uki.sh` signs it (`sbsign`) and writes `output/secureboot/`: the signed
  UKI, OVMF's Secure Boot firmware (`ovmf-code.fd`, from the pinned assembler image),
  and its variable store (`ovmf-vars.fd`) with the certificate enrolled as PK, KEK and db
  and Secure Boot on (`virt-fw-vars`). Without `--key` the key is made for the run and
  thrown away: it proves the mechanism, not who built the image.
- `kaetzli.qemu` with `SECUREBOOT=<dir>` boots that on q35 with SMM, a copy of the
  variables per boot, and the credentials (`login.noauth`, the ssh key) over fw_cfg,
  since the command line is sealed. A `VAR_DISK` goes the same way: `fstab.extra` (the
  mount `systemd.mount-extra=` would make, written to `<disk>.fstab`) and
  `system.machine_id` (`<disk>.machine-id`), so `/var` and the machine ID persist.
- What Secure Boot seals is the kernel, the command line and through it the root: what
  the image *is*. Credentials come with the VMM's invocation, as the disks do, and are
  not sealed; who may log in, and which disk is `/var`, is the caller's to say.

## Who signs: you

Releases carry the UKI **unsigned**, and the project holds no signing key, by design. In a
VM whoever runs the VMM owns the firmware's variables, so a key of ours would prove nothing
that yours does not; what you need from us is that the UKI is the one CI built, and that is
`SHA256SUMS` and its attestation (`tools/fetch-image.sh` checks both) and the UKI being
reproducible. Verify, then sign with your key: that key is the only secret, and it is yours.

## What is checked

CI's boot job (amd64) also boots the launcher with `--secure-boot` on the runner's OVMF,
and checks the kernel reports Secure Boot on, `/var` is its disk and the hostname is the
machine's name.

`test/secureboot.py` (CI's boot job, amd64): the signed UKI boots although the harness
passes `init=/bin/bash`, the kernel reports Secure Boot on, `/proc/cmdline` carries the
image's root hash and not the injected argument, the credentials arrived, and `/var` is
the attached disk with its machine ID; then the
unsigned UKI under the same firmware never starts Linux; and, as the control, the signed
UKI with Secure Boot off (`ovmf-vars-off.fd`) does take the injected `init=` and gives a
shell, so the sealed command line of the first boot is Secure Boot's doing.

## Not yet

- **Extensions.** Their `dm-mod.create=` tables are arguments on the command line the UKI
  seals, so `SECUREBOOT` refuses them. Next: extensions with signed verity root hashes,
  checked against the firmware's db through the kernel's platform keyring, so the user's
  key signs them as it signs the UKI.
- **arm64** (AAVMF with Secure Boot) and **firmware-less VMMs**, which are handed a
  kernel and command line directly and so cannot enforce any of this.
