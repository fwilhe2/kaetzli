# Image variants

**Status: proposal.** The build currently assembles one userspace tree into two formats:
EROFS for VMs and OCI for containers. It does not select different package sets.

This document outlines a way to add smaller or specialized images without compiling every
package again. It is not an implementation guide; the current build is described in
[How the build works](../guides/how-it-works.md).

## Separate image content from output format

Two choices are involved:

- A **variant** selects packages and configuration, such as a minimal system, the current
  full image, or a Kubernetes node.
- A **platform** determines how the selected tree is packaged and booted, such as EROFS or
  OCI.

An image is a variant built for a platform. Package compilation remains independent of
variants; selection happens during assembly. This avoids multiplying package builds as
variants are added.

## Package file manifests

Assembly needs to know which files came from each package. Packages already install into
separate `staging/<package>/` trees, so their file lists can be recorded directly. The
assembler can union the selected manifests and apply platform-specific exclusions.

Do not model selection as a long list of paths to delete. That list would need manual
updates when package versions or SONAMEs change. File manifests also support a useful
diagnostic: identifying which package supplied a file.

## Required checks

Every assembled variant must run dependency and symbol checks against its selected tree.
The full image resolving all dependencies does not prove a smaller image will. Missing
libraries should identify the package to add. Boot tests must also run for each supported
variant; a successful full-image boot does not validate a subset.

Manifest checks cannot detect every missing data file, such as a unit, certificate bundle,
or terminfo entry. Image-content assertions and real boots remain necessary.

## CI and compatibility

Package build jobs and their caches can remain unchanged. Assembly can select package sets
from the uploaded artifacts; tests then run for each supported variant and architecture.
Each variant adds image artifacts, size budgets, and boot coverage, so add one only when it
has a real user.

Keep the existing `kaetzli:latest` and commit tags assigned to the current full image if
variants are introduced. Decide how to name and publish other variants before publishing
them.

## Open decisions

- Define the package lists and supported platforms for each variant.
- Choose and validate a line-oriented configuration format.
- Decide how `/etc` overlays and `extra/` files interact with package selection.
- Set per-variant size budgets and tests before adding variants to CI.
- Consider a self-bootable disk platform separately; it requires a partition table and
  bootloader, unlike the current EROFS filesystem image ([cloud VPS](cloud-vps.md) is
  one use).

## An appliance variant: exec only from dm-verity (IPE)

The general image cannot restrict what runs, because it must run any container
(constraint 3). An appliance variant, with fixed workloads and no `podman pull`, can,
with the kernel's IPE LSM (Integrity Policy Enforcement, mainline since 6.12). It is not
built today: `CONFIG_LSM=` names only what `kspp.config` builds.

The one policy worth having allows `op=EXECUTE` only for files on a dm-verity device:
the root, and the extensions, which are verity too (`dmverity_roothash`). Nothing written
to `/var`, `/tmp` or a home then runs as native code, root included.

- **Pulled containers stop working.** A container's root is overlayfs over layers on
  `/var` (ext4, no verity), so its first `execve` fails with `EACCES`; crun itself, on
  the root, starts. IPE has no subject conditions, so a policy cannot exempt containers.
- **What still runs**: containers whose root is EROFS plus dm-verity built in this
  pipeline and attached like an extension (`podman run --rootfs`), or composefs layers
  whose fs-verity digests are signed by a key the variant enrolls (`fsverity_signature`).
  Registry images are neither.
- **Not covered**: interpreters on the root run scripts from anywhere (`bash /var/x.sh`,
  awk, `nft -f`); IPE sees native code only. `memfd` executables are denied, which some
  JITs and runtimes need.
- **Before building it**: boot an enforcing policy and confirm `podman run alpine true`
  fails as above (IPE reads the property from the file's superblock's block device, which
  overlayfs does not have), and that the policy's audit mode reports what the workloads
  would lose.
