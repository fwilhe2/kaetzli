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
