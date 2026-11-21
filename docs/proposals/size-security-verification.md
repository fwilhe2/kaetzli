# Smaller, more secure, easier to verify (2026-11-21)

**Status: implemented, apart from what is listed under *Open*.** A pass over the image
for fewer files, a harder kernel and checks a user can run without trusting CI. Each
item is one commit on `main`; this page says where each one stands and what is left.
Nothing new is planned here beyond *Open*.

## Done

| Item | Commit | What changed | Verified |
| --- | --- | --- | --- |
| Kernel hardening | `1e423ff` | Landlock and lockdown are now built (CONFIG_LSM named them, but an LSM that is not built is skipped silently); lockdown forced to integrity in the config; KSPP's usercopy, freelist, list and stack checks; `DEVMEM`, `LDISC_AUTOLOAD` off. `test/kernel-hardening.txt` + `kernel-hardening.py` (`!SYM` = must be off), selftest fixtures, rootfs job. | CI green on both arches, boot included (debug tools under lockdown) |
| LSM list | `1e423ff` | `CONFIG_LSM="landlock,lockdown,yama,bpf"`: LoadPin, SafeSetID, IPE dropped (each needs a policy) | same run |
| IPE | `1e423ff` | Not built. Why its one useful policy (exec only from dm-verity) stops pulled containers, and where it would fit: [image variants](image-variants.md#an-appliance-variant-exec-only-from-dm-verity-ipe) | — (docs) |
| kmod | `47d5476` | systemd `-Dkmod=disabled`, `packages/kmod` and `libkmod-dev` gone (`CONFIG_MODULES` is off); `absent` lines in `test/image-files/kernel.txt` | CI green, both arches |
| libstdc++ | `842dace` | Kept on purpose, for prebuilt C++ programs: `present` lines in `test/image-files/gcc.txt` | CI green |
| Immutable releases | `4695b6d` | Were already on; [release.md](release.md) says so, and what it means for expiry (a tag may outlive its release) | `gh api repos/fwilhe2/kaetzli/immutable-releases` |
| Weekly rebuild | `37e104c` | `.github/workflows/rebuild.yml`: the latest release rebuilt with `FRESH=1`, no cache, compared with `SHA256SUMS`; byte-identical assets get a `rebuild/v1` attestation ([reproducing a build](reproducing-a-build.md)) | Not run yet (Sundays 03:00 UTC, or dispatch) |
| arm64 alignment | `f25b3ea`, `167d71f` | `-z max-page-size=16384` on arm64 (`builder/build-package.sh`, glibc's `configparms`): less padding; the OCI image still runs on 4 and 16 KiB hosts, not 64 KiB ones. systemd keeps the default: its EFI stubs need 4 KiB and `elf2efi` refuses anything else — what broke CI between the two commits; budget lowered in `f9e4027` | CI: arm64 erofs tree 88.2 → 75.4 MiB, oci 58.8 → 47.0 (amd64: 75.0, 49.1) |
| vTPM | `a305875`, `37e824d` | `TCG_TPM`, `TCG_TIS`, `TCG_CRB` in the kernel (`kernel-caps.txt`); the kernel fixtures carry them | CI green, both arches |
| Measured boot | `14fe13a` | systemd's stub `-Dtpm=true` (no tpm2-tss); `image/pcr.py` predicts PCR 11 (matches `systemd-measure calculate`); `kaetzli.pcr.json` → release asset `uki-<arch>.pcr.json`; `test/secureboot.py` boots under swtpm and requires PCR 11 = prediction = published value ([Secure Boot](../guides/secure-boot.md#measured-boot)) | CI (run 44934567492): the guest's PCR 11 under swtpm was the predicted and published value |
| Launcher attestation | `7b543e7` | `go/internal/attest`: Sigstore bundle verified with the standard library (Fulcio chain, ci.yml on main, DSSE, Rekor SET, inclusion proof, checkpoint); `kaetzli update` refuses an unattested snapshot | Go tests; live against `20261121.083317-915` |

## Not changed, on purpose

- **gconv modules**: what is left is Western and Unicode charsets, reachable by `iconv(1)`
  and user programs; nothing in the image needs more, and less would remove something
  useful rather than something unreachable.
- **arm64 Secure Boot**: still blocked by qemu's arm64 having no SMM
  ([Secure Boot](../guides/secure-boot.md#not-yet)).

## Open

1. **Review the pinned Sigstore root**: `go/internal/attest/trusted_root.json` is a trust
   anchor, committed from `gh attestation trusted-root`; like the toolchain keys it
   should be a person's commit (or reviewed as one).
2. **First `rebuild.yml` run**: unmeasured in one job; a fresh CI build's serial path is
   ~2 h, inside its 350-minute timeout.
