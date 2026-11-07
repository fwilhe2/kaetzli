# Build reproduction

**Status: partly implemented.** Implemented: an independent end-to-end rebuild of a
release, `tools/rebuild-release.py` (below). Not yet: a recipe artifact.

Archiving the full build recipe as an artifact remains a proposal. Package source patches
and upstream patch series are implemented; see the [patches guide](../guides/patches.md).
This page describes the current reproducibility checks and the remaining recipe proposal.

## Current reproducibility

The build pins package source archives by SHA256, pins the Debian builder to a snapshot,
and uses a fixed `SOURCE_DATE_EPOCH`. Image assembly normalizes timestamps and other
metadata. `test/reproducible.sh <package>` builds a package twice from clean worktrees and
compares staged paths, modes, owners, symlink targets, and file contents. CI also assembles
the images a second time and compares the EROFS image, command line, OCI archive, SBOMs, and
extension images byte for byte.

To compare a local package build with a CI artifact for the same commit, use:

```sh
AGAINST_RUN=<github-actions-run-id> ./test/reproducible.sh <package>
```

This verifies only the selected package against the CI artifact. It does not establish that
every package has been independently rebuilt on another machine. CI's image check repeats
assembly from the same build artifacts, so it checks deterministic assembly rather than an
independent end-to-end rebuild.

## Rebuilding a release

```sh
./tools/rebuild-release.py 20261107.171105-764          # hours the first time
./tools/rebuild-release.py 20261107.171105-764 --no-build   # compare again
```

Each snapshot is a tag on the commit it was built from. The tool checks that commit out
into a clean worktree (`output/rebuild/<name>/`), builds everything there as CI did
(`tools/build-image.sh`: glibc, the kernel, every package against the glibc-only sysroot,
assembly with the snapshot name as `KAETZLI_VERSION` and the commit as `KAETZLI_BUILD`),
and compares every asset of the host's architecture with the release's `SHA256SUMS`. Asset
names come from `kaetzli.release.collect`, the code that named the published ones. It
exits 0 only when every asset a build makes is byte-identical; the CVE workbook (a dated
scan), the source tar, the launchers and the other architecture's assets are listed as not
rebuilt and not judged. The builder image and sources image are pulled by the commit's own
content-hash tags, so the toolchain is the one CI used.

## Proposed recipe artifact

The builder and sources images preserve the toolchain and upstream archives. A recipe
artifact could preserve the tracked build scripts and configuration that combine those
inputs. It should be deterministic, identify the source commit, and record whether the
working tree was modified. A digest of the recipe could then be referenced by the image
SBOM.

Keep the recipe separate from the sources image: source archives change when package pins
change, while build scripts can change on every commit. Combining them would either
invalidate the sources cache for unrelated edits or make a content-addressed tag describe
inconsistent contents.

An end-to-end tool could extract the recipe, retrieve its builder and sources images, then
rebuild packages and images. It must report exactly which artifacts and checksums it used.
Until this exists, an image is not independently reconstructible from its OCI reference
alone; the repository commit and build artifacts remain relevant inputs.

Any future recipe archive should include patches and all other tracked build inputs. It
should identify the source commit and inputs used, and support rebuilding the published
artifacts with explicit checksums. Until that exists, an OCI reference alone is not enough
to independently reconstruct an image; the repository commit and build artifacts remain
relevant inputs.
