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

Every green build on `main` publishes one GitHub release, so OCI `latest` and the latest
release are always the same commit.

- **Name**: `YYYYMMDD.HHMMSS`, the commit's committer date in UTC, e.g.
  `20261103.141205`. Names sort, as strings and as versions, in the order the commits
  reached `main`. They are derived from the commit, so the same commit always gets the
  same name, and a reproducibility rebuild with the same name gives the same bytes. The
  commit is in the release notes and in the image (`BUILD_ID`), not in the name.
- **Latest**: each new snapshot is marked as the repository's latest release, so
  `https://github.com/fwilhe2/kaetzli/releases/latest/download/<file>` is the rolling
  URL. A snapshot is a full release, not a pre-release, because GitHub's latest skips
  pre-releases.
- **Immutable**: a published snapshot's assets and tag never change. Enable GitHub's
  immutable releases setting so this is enforced, not just intended.
- **Expiry**: snapshots and their tags are deleted after 30 days, the latest never. They
  exist for bisecting a regression, not for pinning.
- **Only forward**: a snapshot is published only if its commit descends from the current
  latest's and is not the same commit. A run that finishes after a newer one does not
  publish, and the scheduled fresh rebuild of an unchanged `main` does not either.

The name goes into `/usr/lib/os-release` as `IMAGE_VERSION=`, systemd's field for
image-based systems. `build-rootfs.sh` currently writes `KAETZLI_VERSION` as `VERSION` and
`VERSION_ID`, which describe a distribution release, and should stop. Extensions match
on `SYSEXT_LEVEL`, so the name does not affect which extensions attach.

## Assets

Only what someone booting, extending or auditing the image needs. Asset names carry the
architecture (`amd64`, `arm64`) but not the snapshot, so the `latest/download/` URLs stay
the same from one snapshot to the next.

| Asset | What it is |
| --- | --- |
| `rootfs-<arch>.erofs` | The root filesystem with its dm-verity hash tree. |
| `rootfs-<arch>.cmdline` | The kernel command line, including the root hash. The image is only usable with it. |
| `kernel-<arch>` | `bzImage` on amd64, `Image` on arm64. |
| `<ext>-<arch>.sysext.raw`, `.sysext.dm-verity` | Each system extension (`podman`, `kubernetes`, `debug`) and its verity table. |
| `<ext>-<arch>.confext.raw`, `.confext.dm-verity` | Each extension's configuration extension and its verity table. |
| `sbom-<arch>.json` | SPDX 2.3 for the disk image. |
| `sbom-oci-<arch>.json` | SPDX 2.3 for the OCI image, which is pulled from the registry. |
| `sbom-ext-<ext>-<arch>.json` | SPDX 2.3 for each extension. |
| `vex-<arch>.json` | OpenVEX for the CVEs our patches fix, so a scanner that reads the SBOM does not report them open. |
| `cve-<arch>.ods` | The CVE workbook (`test/cve-workbook.sh`): findings by severity, vulnerability and package, patched ones marked. A scan as of publication, with the grype database date in the notes. |
| `SHA256SUMS` | Every other asset, for both arches, in `sha256sum -c` format. |

These stay out:

- The OCI archive: the registry distributes the OCI image.
- grype's JSON reports and `cve-summary.txt`: the workbook is their readable form, and
  the SBOMs and VEX let anyone rescan with a current database.
- CI's `bundle-<arch>` manifest: `SHA256SUMS` replaces it for releases.
- Test logs, size reports and test binaries.

## Release notes

1. The rolling notice:
   > Rolling snapshot. No fixes are backported; the only fix is a newer snapshot. Deleted
   > after 30 days.
2. The commit, the CI run, and the builder and sources images by digest. The sources
   image holds every pinned tarball and patch, the corresponding source for the copyleft
   packages, and is kept for at least as long as the snapshot exists.
3. What changed since the previous snapshot: `test/image-diff.py`'s package and size
   report, run against that snapshot rather than main's previous green build.
4. How to boot it with qemu, using the assets by their `latest/download/` URLs.

## Fetching

`tools/fetch-image.sh` (and `vm.sh update`) uses `curl` and no authentication:

1. Resolve which snapshot is latest from the redirect of `releases/latest`, once.
2. Download `SHA256SUMS` and the assets from that snapshot's URL rather than
   `latest/download/`, so a snapshot published mid-download cannot mix two builds.
3. Verify every file against `SHA256SUMS` before installing anything, as the manifest
   check does today, and record the snapshot name beside the image.

`--snapshot <name>` fetches an older one. The current CI artifact path stays, behind
`--run <id>`, for pull requests and debugging.

## Publishing

A `release` job in `ci.yml`, after `boot`, `reproducible` and `cve-scan`, on pushes to `main` only,
with `concurrency: release` so two runs never publish at once. It calls
`tools/release.sh`, which can also run against a fork:

1. Compute the name from the commit. Skip if the commit is not ahead of the latest
   snapshot's.
2. Collect the assets from the run's artifacts under their release names, and write
   `SHA256SUMS`. The workbook is the one optional asset: `cve-workbook.sh` never fails a
   build, so a missing one is a line in the notes, not a missing snapshot.
3. Create a draft release with that tag on that commit, upload every asset, write the
   notes.
4. Publish it and mark it latest. Nothing is visible before this step.
5. Delete snapshots older than 30 days and their tags, never the latest.

The image is assembled with `KAETZLI_VERSION` set to the name, so the reproducibility
job must set it too.
