# Documentation

Guides describe current behavior. Proposals describe work that is not implemented.

## Guides

- [What kaetzli is](what-kaetzli-is.md): the design choices and how each is enforced, for
  people who already build images.
- [How the build works](guides/how-it-works.md): package builds, assembly, and CI.
- [Build inputs](guides/build-container.md): builder and sources images.
- [Patches](guides/patches.md): package patches, upstream patch series, and CVE fixes.
- [Linking](guides/linking.md): runtime library compatibility and ELF checks.
- [System extensions](guides/sysext.md): build and attach extensions.
- [Debugging](guides/debugging.md): tools in the base and debug extension, expected boot
  messages, and how login and SSH access work.

## Proposals

- [Build dependencies](proposals/extension-dependencies.md): compile packages against
  other packages built in this repository.
- [Image variants](proposals/image-variants.md): select package sets at assembly time.
- [Cloud VPS](proposals/cloud-vps.md): kaetzli as the system a VPS provider boots; which
  providers fit, and the disk image that is missing.
- [macOS container host](proposals/container-host.md): use the image as a Podman VM.
- [Releases](proposals/release.md): rolling, expiring snapshots as GitHub releases; no supported versions.
- [Always latest](proposals/always-latest.md): moving machines and Kubernetes nodes to
  every new snapshot by replacement, and what is missing for that.
- [Launcher](proposals/launcher.md): one downloaded binary that runs machines on Linux,
  macOS and Windows, without a checkout.
- [Build reproduction](proposals/reproducing-a-build.md): archive the recipe as a future
  improvement; package patches are already supported ([guide](guides/patches.md)).
- [Ideas from other builders](proposals/ideas-from-other-builders.md): checks, hardening
  and SBOM features adapted from Talos, LinuxKit, Flatcar and podman-machine-os.
- [Host-side scripts](proposals/host-scripting.md): which tests and tools move from bash
  to Python, and in what order.

Host architecture and tool requirements are summarized in the [project README](../README.md#host-requirements).
