# A distribution builder

**Status: idea, nothing decided.** This is a sketch for later review, not a plan. It asks
whether kaetzli could become a small, general-purpose distribution builder, similar in
purpose to Buildroot but simpler to use and aimed at VMs and containers rather than
embedded boards. Nothing here is implemented, and nothing here changes the constraints in
`CLAUDE.md`.

## The idea

Most of a builder is already here: packages as `env.sh` + `build.sh`, one containerised
toolchain, staging trees merged into a rootfs, an assembly step that writes several output
formats, generated SBOMs, and checks on the tree. What is missing is a declarative input.
Today, "which image" is spread across `packages/`, `image/files/`, the kernel fragments and
the trim and OCI blocks in `image/build-rootfs.sh`. A builder would put that in one file:

```toml
# my-node.toml
[base]                       # fixed: glibc, systemd, bash, coreutils; tuned, never removed
arch = ["amd64", "arm64"]

[systemd]
features = ["networkd", "resolved", "repart", "sysext"]   # homed, boot, … off

[packages]
include = ["curl", "openssh", "iproute2"]
extensions = { podman = ["podman", "netavark", "passt"] }

[kernel]
features = ["containers", "bpf"]       # names for fragment bundles, not raw CONFIG_

[output]
formats = ["erofs-verity", "oci", "qcow2"]

[files]
overlay = "./etc-overlay"   # like today's --extra, held to the same rules
```

`kaetzli build my-node.toml` would be a Python CLI (`tomllib` is in the 3.12 standard
library, so no new dependency). It would resolve the profile, work out which packages need
building with which features, run each package's containerised `build.sh` as today, and
assemble. The host would need only podman and `python3`.

What would stay fixed:

- systemd, glibc, GNU coreutils and bash are always present. Only their features are
  configurable.
- Only curated packages: user profiles choose from `packages/` and do not bring their own
  (at least at first).
- VMs and containers are the only targets. Output formats are VM and container formats,
  not board images. This is the clearest difference from Buildroot.

## Two levels of configuration

**1. Selection: which packages and extensions go in.** Selection is cheap: nothing is
recompiled, and the image is a different merge of existing staging trees.
[Image variants](image-variants.md) already proposes this (variants, platforms, package
file manifests). Most of what users want, such as "curl but not podman", is this level.
It keeps the binary cache fully shared.

**2. Features: how a package is compiled.** Examples are systemd components, kernel
symbols and curl protocols. Features are expensive: each combination is a separate build
and cache entry, and a combination nobody has tried may not compile.

Ship level 1 first, and level 2 only for the packages where it clearly pays off: systemd
and the kernel, perhaps curl. Avoid Gentoo-style USE flags on every package. Much of what
makes Buildroot and Yocto hard to use is thousands of options, most of them untested.

## What a feature is

A feature would be a named bundle declared by the package, not a raw configure flag passed
through from the profile. It records what the repository now knows only implicitly:

```bash
# packages/systemd/features.sh  (bash, like env.sh)
FEATURE_resolved_MESON="-Dresolve=true"
FEATURE_resolved_PRESENT="usr/lib/systemd/systemd-resolved"
FEATURE_homed_MESON="-Dhomed=enabled"
FEATURE_homed_KERNEL="dm-crypt"              # a kernel feature it needs
FEATURE_homed_DEFAULT=off
```

This generalizes what exists:

- `present` and `absent` lines in `test/image-files/` become conditional on features. A
  feature that is off means its files are absent, which still catches a version bump that
  silently turns it back on.
- Kernel fragments (`container`, `k8s`, `trace`) become kernel features that packages
  require. "podman requires `kernel:containers`" replaces the knowledge kept in
  `test/kernel-caps.txt`.
- `EXTENSION=` stays as it is.

Dependency resolution should validate and fail with a message rather than use a solver.
"podman needs kernel feature `containers`; add it" is enough.

## Constraints become a validator

The fixed decisions move from review rules into checks that run on every profile:

- The profile schema cannot express removing glibc, systemd, bash or coreutils. Some of
  their features are always on (journald, PID 1, udev in the disk flavour).
- DFSG licenses, no interpreters, nothing setuid, reproducibility, hardening, symbol
  versions and unresolved libraries are already checked on a tree, so they run on any
  profile's output. Users cannot turn them off, which is what would set this apart.

## Output formats

`image/build-rootfs.sh` already has flavours: disk, and OCI as a block of subtractions.
Generalized, an output would be:

- a **tree flavour**: what is removed from the merged tree (OCI's subtractions become a
  built-in rule: no kernel, no udev, …);
- a **packer**: EROFS with dm-verity and OCI (both exist today); a raw GPT disk or qcow2
  made by `systemd-repart`, which our systemd already provides; a UKI (`ukify` is Python
  but runs only at build time, which is allowed; `systemd-measure` is another option).

Each packer writes its command line and hash outputs next to the image, as
`rootfs.cmdline` does today, and gets an SBOM per output.

## Testing

Today's quality comes from one image being tested thoroughly. A configurable build cannot
test every combination, so the levels must be explicit:

- **Profiles in the repository** (for example `base`, `container-host`, `k8s-node`,
  `kiosk`) are what CI builds and boots. Today's image becomes `profiles/base.toml` plus
  its extensions, so nothing is lost.
- **Boot tests declare what they need.** `network.py` needs `networkd` and `resolved`, and
  `podman.sh` needs the podman extension. The runner picks the applicable tests for a
  profile, which gives users `kaetzli test my-node.toml`.
- **User profiles** always get every static check, plus the boot tests that apply. An
  untested combination is documented as untested.
- Size budgets are kept per profile.

## Caching

Today's cache key covers the build scripts, the pins and the builder tag. It would also
include a hash of the package's resolved feature set. Profiles with the same feature set
share artifacts, so the published reference builds would serve as a binary cache for most
user profiles. Staging artifacts could live in the registry beside the builder and source
images, which are already content-addressed.

## Prior art to read first

- **Bottlerocket**: the closest match. It is built from source, aimed at VMs and container
  hosts, and has *variants* (named, tested profiles). Its variants are curated rather than
  composed by users; find out why.
- **mkosi**: systemd's image builder. It builds from distribution packages, not source,
  but its output side (repart, UKI, verity, sysexts) is what this needs. If it can take a
  prepared tree as input, it could be the packer layer instead of a rewrite. Not checked.
- **Gentoo USE flags**: the feature model, including how it fails (combinations that do
  not build).
- **Buildroot `BR2_EXTERNAL`, Yocto layers**: packages kept outside the repository.
  Defer, but keep the layout open to a profile naming an extra `packages/` directory.

## Risks

- **Losing the opinion.** The constraints and the questions a new package must answer are
  what make this a distribution. With unlimited options it becomes a worse Buildroot.
  Keep the package set curated and configuration subtractive and feature-level.
- **Library features breaking consumers.** Examples are curl without a protocol, or
  systemd without parts of `libsystemd` that crun links against. The symbol checks catch
  this at build time, but it is a reason to keep library features rare.
- **The tool and the distribution pulling apart.** They differ in documentation, CI and
  release cadence. Start as one repository with reference profiles.

## A possible order

1. Express today's image as `profiles/base.toml` with byte-identical output: a pure
   refactor, proved by the reproducibility check.
2. Select packages and extensions from a profile, with no features yet ([Image
   variants](image-variants.md)). Add a second profile in the repository, for example a
   minimal one with no extensions and OCI output only.
3. Boot tests declare what they need, and the runner selects tests by profile.
4. Features for systemd and the kernel only.
5. Packers: `systemd-repart` disks and qcow2, then a UKI.
6. Packages outside the repository, if anyone asks for them.

## Open questions

- Who is the user: the maintainer building variants, or other people writing profiles?
  This decides how much validation and documentation the profile format needs.
- May user profiles add packages from outside the repository, or only choose from the
  curated set? The second is much simpler and keeps the license constraint enforceable.
- Do a few named profiles (container host, Kubernetes node, kiosk) cover most of the
  need? If so, step 2 is most of the value, and features may never be needed.
