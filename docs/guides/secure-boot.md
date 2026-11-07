# Secure Boot

**Status: implemented for amd64 under qemu (OVMF).** Not yet: arm64, release assets signed
with a published key, a persistent `/var` or system extensions in this mode, and VMMs that
boot without firmware (Firecracker, Virtualization.framework).

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

## Build, sign, boot

```sh
./tools/build-image.sh                 # also writes output/kaetzli.efi, unsigned
./tools/sign-uki.sh                    # a throwaway key; or --key K.pem --cert C.pem
SECUREBOOT=output/secureboot ./tools/boot-qemu.py   # any kaetzli.qemu boot
./test/secureboot.py                   # what CI checks
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
  since the command line is sealed.

## What is checked

`test/secureboot.py` (CI's boot job, amd64): the signed UKI boots although the harness
passes `init=/bin/bash`, the kernel reports Secure Boot on, `/proc/cmdline` carries the
image's root hash and not the injected argument, and the credentials arrived; then the
unsigned UKI under the same firmware never starts Linux. With Secure Boot off in the
variables the test fails, naming it.

## Not yet

- **`/var` disk and extensions.** Both are arguments on the command line the UKI seals
  (`systemd.mount-extra=`, the extensions' `dm-mod.create=` tables), so `SECUREBOOT`
  refuses them. Next: a `/var` found by label from the UKI's own command line, and
  extensions with signed verity root hashes, checked against the firmware's db through
  the kernel's platform keyring.
- **Releases.** Signing with a key users can trust needs a CI secret and a published
  certificate; until then releases carry no signed UKI.
- **arm64** (AAVMF with Secure Boot) and **firmware-less VMMs**, which are handed a
  kernel and command line directly and so cannot enforce any of this.
