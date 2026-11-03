# Releases

**Status: proposal.** The repository publishes the OCI image from `main`, but has no
downloadable VM image release. `tools/fetch-image.sh` reads CI artifacts, which need an
authenticated `gh` and expire.

## The model is rolling

kaetzli has no stable releases and no backported fixes. The only fix for anything is a
newer build. There will be no `1.0`: a version number would promise support that does not
exist. If you cannot always run the latest build, kaetzli is not for you.

Releases exist for distribution, not support: a stable URL that needs no authentication,
a page that lists what was built, and files that outlive CI's artifact retention.

## Snapshots

Every green build on `main` publishes one GitHub release, at the same point as
`publish-oci` and from the same commit, so OCI `latest` and release `latest` always agree.

- **Tag**: `YYYYMMDD-<short commit>`, e.g. `20261103-fbc0c39`. The date orders them; the
  commit says what they are and keeps two builds on one day apart. It is an identifier,
  not a version.
- **Immutable**: a published snapshot's assets are never replaced.
- **Latest**: each new snapshot is marked as the repository's latest release, so
  `https://github.com/fwilhe2/kaetzli/releases/latest/download/<file>` is the rolling
  URL. Moving that pointer switches every file at once. A mutable release whose assets are
  re-uploaded one by one would not: a download in between could pair a new
  `rootfs.erofs` with an old `rootfs.cmdline`, and the root hash would not match.
- **Expiry**: snapshots and their tags are deleted after 30 days. They exist for
  bisecting a regression, not for pinning.
- **No empty snapshots**: none is published when the commit is the same as the previous
  snapshot's.

Every release note starts with:

> Rolling snapshot. No fixes are backported; the only fix is a newer snapshot. Deleted
> after 30 days.

## Assets

For each architecture, the files a boot needs, named with the arch:

- `rootfs-<arch>.erofs` and `rootfs-<arch>.cmdline` (the latter carries the dm-verity
  root hash, so the two are only usable together);
- the kernel image;
- the system and configuration extensions with their `.dm-verity` files;
- the SBOMs;
- `bundle-<arch>`: the manifest the rootfs job already writes (run, commit, arch, the
  SHA-256 of every file).

The EROFS file is a filesystem, not a bootable disk; the notes give the boot command for
each supported VMM.

## OCI tags

`kaetzli:latest` keeps its current meaning, the newest green build of `main`, and the
per-commit tags stay. Each snapshot also tags `kaetzli:<snapshot tag>`. There is no `edge`
tag: under a rolling model, latest is edge.

## Fetching

`tools/fetch-image.sh` (and `vm.sh update`) downloads `bundle-<arch>` and then the files it
names from `releases/latest/download/`, using `curl` and the same strict manifest check as
today. This removes `gh` from the quick start's requirements. The current CI artifact path
stays, behind `--run <id>`, for pull requests and debugging.

## Provenance and source

Each release note names the source commit, the CI run, and the builder and sources images
by digest. The sources image holds every pinned tarball and patch, which is the
corresponding source for the copyleft packages. Keep that image for at least as long as
the snapshot exists. The OCI registry already distributes the same binaries, so releases
add no new obligation, but they make the existing one more visible.

## Implementation notes

A thin workflow step calls a script that can also run locally against a fork. The script
creates the release as a draft, uploads every asset, publishes the release and marks it
latest, then prunes expired snapshots. The snapshot tag is computed once, so the release,
the OCI tag, `KAETZLI_VERSION` in the image and the notes all use the same value.
