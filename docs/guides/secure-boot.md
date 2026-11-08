# Secure Boot

**Status: implemented for amd64 under qemu (OVMF), with a persistent `/var` and
extensions: the launcher (`kaetzli start --secure-boot`) needs no key, and you can sign
the UKI with your own.** Not yet: arm64. Out of reach by design: VMMs that boot without
firmware (Firecracker, Virtualization.framework).

## Why

dm-verity makes the root read only what its root hash describes, and the root hash is on
the kernel command line. Whoever supplies the command line chooses the hash, so verity
alone proves the root matches *a* hash, not *ours*. The kernel cannot check a signature on
the hash here: dm-verity reads one only from a keyring that userspace fills, and the root
is set up by `dm-mod.create=` before there is any userspace (and there is no initrd).

A **unified kernel image** closes that: the kernel, the command line with the root hash,
and systemd's EFI stub in one PE binary. Under Secure Boot the firmware runs only an
image its db admits, by a key that signed it or by the image's own digest, and the stub
then ignores any command line the VMM passes. A different root hash means a different
UKI, which db does not admit.

## With the launcher

```sh
kaetzli start --secure-boot --ext podman   # remembered; --no-secure-boot turns it off
```

- **No key.** Each start checks the snapshot's UKI and the attached extensions' verity
  tables against its `SHA256SUMS` again (not only when it was fetched; an `--image`
  directory is your own build and is not checked), writes the tables into the UKI, and
  writes the machine's firmware variables fresh from the template of the host's OVMF,
  found through qemu's firmware descriptors (`go/internal/firmware`): **that UKI's
  Authenticode digest** in db, and a PK and KEK made for the machine and thrown away
  (`go/internal/efivars`). The firmware then boots exactly that image: not another one,
  and not the machine's UKI of an earlier start either, which a key that signed both would
  still let in. Nothing but qemu and OVMF on the host (Debian, Ubuntu: `ovmf`; Fedora:
  `edk2-ovmf`). Launchers from before made `secureboot/db.key` in their data directory;
  nothing uses it now, and it can go.
- **Extensions** are verified by the kernel as without Secure Boot, from their dm-verity
  tables, which the launcher writes into the UKI's own command line (its `.cmdline`
  section, `go/internal/uki`) before taking its digest: the digest covers the tables, the
  tables (verified against `SHA256SUMS` with the snapshot) pin the extensions' bytes. A
  different set of extensions is a different UKI, admitted at that start. The tables
  share the kernel's 2047 bytes with the root's, which the launcher checks.
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
- `tools/sign-uki.sh` signs it and writes `output/secureboot/`: the signed UKI, OVMF's
  Secure Boot firmware (`ovmf-code.fd`, from the pinned assembler image), and its
  variable store (`ovmf-vars.fd`) with a PK and KEK made for it and thrown away, the
  certificate in db and Secure Boot on. The signing and the store are the launcher's
  code (`kaetzli secureboot-files`, run in the golang container `go/go.mod` names): one
  implementation, which `go.yml` holds to `sbverify` and `virt-fw-vars`. Without
  `--key` the key is made for the run and never written: it proves the mechanism, not
  who built the image. `--ext podman,debug [--var]` writes those extensions' tables into
  the UKI's command line first, as the launcher does, and records it in `sealed`;
  `kaetzli.qemu` then boots `SYSEXTS` under Secure Boot, and refuses a boot whose
  extensions or `/var` disk differ from what the UKI was sealed for (the disks' names
  are in the tables).
- `kaetzli.qemu` with `SECUREBOOT=<dir>` boots that on q35 with SMM, a copy of the
  variables per boot, and the credentials (`login.noauth`, the ssh key) over fw_cfg,
  since the command line is sealed. A `VAR_DISK` goes the same way: `fstab.extra` (the
  mount `systemd.mount-extra=` would make, written to `<disk>.fstab`) and
  `system.machine_id` (`<disk>.machine-id`), so `/var` and the machine ID persist.
- What Secure Boot seals is the kernel, the command line and through it the root: what
  the image *is*. Credentials come with the VMM's invocation, as the disks do, and are
  not sealed; who may log in, and which disk is `/var`, is the caller's to say.
- **Nothing on `/var` is merged as an extension.** `/var` is a writable disk nothing
  verifies, and systemd merges `/var/lib/extensions` and `/var/lib/confexts` without
  dm-verity, so a change to that disk would run unsealed code. Under Secure Boot
  `var-lib-extensions.mount` and `var-lib-confexts.mount` (`ConditionSecurity=uefi-secureboot`,
  started with `systemd-sysext` and `systemd-confext`) cover both with an empty
  read-only tmpfs; `test/secureboot.py` checks it. The data on `/var` is still yours to
  protect: Secure Boot vouches for the code, not for what that code reads.

## Who signs: nobody, or you

Releases carry the UKI **unsigned**, and the project holds no signing key, by design. In a
VM whoever runs the VMM owns the firmware's variables, so a key of ours would prove nothing
that yours does not; what you need from us is that the UKI is the one CI built, and that is
`SHA256SUMS` and its attestation (`tools/fetch-image.sh` checks both) and the UKI being
reproducible.

The launcher writes a machine's variables at every start, so it enrolls the image itself
and needs no key at all. A key is for firmware whose variables outlive one boot and that
you do not rewrite: sign the UKI with yours (`tools/sign-uki.sh --key K.pem --cert C.pem`)
and enroll its certificate. That key is then the only secret, and it is yours.

## What is checked

CI's boot job (amd64) also boots the launcher with `--secure-boot --ext podman` on the
runner's OVMF, and checks the kernel reports Secure Boot on, `/var` is its disk, the
hostname is the machine's name and podman merged.

`test/secureboot.py` (CI's boot job, amd64): the signed UKI boots although the harness
passes `init=/bin/bash`, the kernel reports Secure Boot on, `/proc/cmdline` carries the
image's root hash and not the injected argument, the credentials arrived, and `/var` is
the attached disk with its machine ID; then the
unsigned UKI under the same firmware never starts Linux; and, as the control, the signed
UKI with Secure Boot off (`ovmf-vars-off.fd`) does take the injected `init=` and gives a
shell, so the sealed command line of the first boot is Secure Boot's doing.

## Not yet

- **arm64.** AAVMF has a Secure Boot build, but arm64 has no SMM: unless qemu also runs
  a secure world, the guest itself can rewrite db, so it would enforce less than on
  amd64, and the guide would have to say so.

## Out of reach, by design

**Firmware-less VMMs** (Firecracker, and Virtualization.framework's direct Linux boot)
are handed a kernel and a command line, with no firmware to check either, so whoever
starts the VMM chooses the root hash. There, verity proves the root matches *a* hash; the
VMM's invocation is what you trust.
