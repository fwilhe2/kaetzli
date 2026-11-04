# Releases

**Status: implemented**: publishing (`tools/release.py`, the `release` job in `ci.yml`)
and fetching (`tools/fetch-image.sh`, `--snapshot`, `--run`). Immutable releases are a
repository setting, to be switched on by hand.

## The model is rolling

kaetzli has no stable releases and no backported fixes. The only fix for anything is a
newer build. There will be no `1.0`: a version number would promise support that does not
exist. If you cannot always run the latest build, kaetzli is not for you.

Releases exist for distribution, not support: a stable URL that needs no authentication,
a page that lists what was built, and files that outlive CI's artifact retention.

## Snapshots

Each eligible green build on `main` publishes a GitHub release. A release job publishes
only if its commit is still what `main` builds: its head, or an ancestor with nothing
but documentation after it. A docs-only push skips the build, so its own commit never
has an image; requiring the head itself would leave the build before it unreleased
until the next change to code. Superseded builds do not get a release. The latest
release therefore tracks the newest green build of what `main` is.

- **Name**: `YYYYMMDD.HHMMSS-<run_number>`, using the commit's committer date in UTC
  and the release workflow's GitHub Actions `run_number`, e.g. `20261103.141205-42`.
  The timestamp is readable metadata; it does not establish ordering. The run number is
  unique and increasing within the long-lived release workflow, though skipped runs leave
  gaps. Retries of the same workflow run keep the same name. Keep the workflow identity
  stable because GitHub resets run numbers if a workflow is recreated or renamed. The
  commit is in the release notes and in the image (`BUILD_ID`), not in the name.
- **Latest**: each new snapshot is marked as the repository's latest release, so
  `https://github.com/fwilhe2/kaetzli/releases/latest/download/<file>` is the rolling
  URL. A snapshot is a full release, not a pre-release, because GitHub's latest skips
  pre-releases.
- **Immutable**: a published snapshot's assets and tag never change. Enable GitHub's
  immutable releases setting so this is enforced, not just intended.
- **Expiry**: snapshots and their tags are deleted after at least 37 days, the latest
  never. Thirty days is the minimum availability period; the extra week gives downloads
  already in progress time to finish. They exist for bisecting a regression, not for
  pinning.
- **Only forward**: before publishing, the job checks that its commit is still what
  `main` builds (above), descends from the latest snapshot's commit, and changes
  something the build reads since it. A stale run skips publication; the current
  head's successful run can publish later. The scheduled fresh rebuild of an unchanged
  `main`, or of one changed only in documentation, does not publish another snapshot.
  "Documentation" is what `ci.yml`'s `changes` job skips the build for, and the two
  must agree. A latest snapshot that is *not* an ancestor of `main` means `main` was
  rewritten: no later commit could ever pass the check, so the job fails for a human
  instead of skipping quietly forever.

The name goes into `/usr/lib/os-release` as `IMAGE_VERSION=` (and `PRETTY_NAME`), on
`main` only, where the rootfs and reproducible jobs both assemble with
`KAETZLI_VERSION=$(tools/release.py name)`. A branch's image is not a release of
anything and carries no name. Every build on `main` carries one, including a build
that is then superseded and never published, so a name in an image is the name it
*would* have, not proof that a release exists. Extensions match on `SYSEXT_LEVEL`, so
the name does not affect which extensions attach.

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
| `<ext>-<arch>.confext.raw`, `.confext.dm-verity` | The configuration extension and its verity table, for an extension that has one (one that stages `etc/`; today `kubernetes`). |
| `sbom-<arch>.json` | SPDX 2.3 for the disk image. |
| `sbom-oci-<arch>.json` | SPDX 2.3 for the OCI image, which is pulled from the registry. |
| `sbom-ext-<ext>-<arch>.json` | SPDX 2.3 for each extension. |
| `vex-<arch>.json` | OpenVEX for the CVEs our patches fix, so a scanner that reads the SBOM does not report them open. One per arch, merged from every SBOM's `fix` references by the release job itself; absent when no patch fixes a CVE. |
| `cve-<arch>.ods` | The CVE workbook (`test/cve-workbook.sh`): findings by severity, vulnerability and package, patched ones marked. A scan as of publication, with the grype database date in the notes. |
| `SHA256SUMS` | Every other asset, for both arches, in `sha256sum -c` format. |

All listed assets are required for both architectures except `cve-<arch>.ods`, which is
optional because the `cve-scan` job gates nothing, and the two conditional ones above.
"Expected" is independent of what the run happened to build: every extension a
package's `EXTENSION=` names needs its sysext, verity table and SBOM, and half a
confext is as wrong as none. Missing any of these fails the release job before a
draft is created. The VEX is not taken from `cve-scan` for the same reason the
workbook is optional: a job that gates nothing must not be what a release depends on. The checksum manifest detects
accidental corruption; by itself it does not authenticate assets if both the files and
manifest are replaced. Authentication is already there: CI's publish-oci job attests
every bundle file by its checksum, and a release asset is the same bytes, so
`gh attestation verify <asset> -R fwilhe2/kaetzli` authenticates it.

These stay out:

- The OCI archive: the registry distributes the OCI image.
- grype's JSON reports and `cve-summary.txt`: the workbook is their readable form, and
  the SBOMs and VEX let anyone rescan with a current database.
- CI's `bundle-<arch>` manifest: `SHA256SUMS` replaces it for releases.
- Test logs, size reports and test binaries.

## Release notes

1. The rolling notice:
   > Rolling snapshot. No fixes are backported; the only fix is a newer snapshot. Deleted
   > after at least 37 days.
2. The commit, the CI run, and the builder and sources images by digest. The sources
   image holds every pinned tarball and patch, the corresponding source for the copyleft
   packages, and is kept for at least as long as the snapshot exists.
3. What changed since the previous snapshot: `test/image-diff.py`'s package report, from
   that snapshot's SBOM assets rather than main's previous green build, and each image's
   size against the previous release's asset sizes (the size reports and manifests
   image-diff also reads are not assets).
4. How to boot it with qemu, using the assets by their `latest/download/` URLs.

## Fetching

`tools/fetch-image.sh` (and `vm.sh update`) uses `curl` and no authentication:

1. Resolve which snapshot is latest from the redirect of `releases/latest`, once.
2. Download `SHA256SUMS` and the assets from that snapshot's URL rather than
   `latest/download/`, so a snapshot published mid-download cannot mix two builds.
3. Verify every file against `SHA256SUMS` before installing anything, as the manifest
   check does today, and record the snapshot name beside the image. If a download fails,
   retry that same snapshot URL; do not switch snapshots partway through. If that
   snapshot has expired, report the failure and let the user start a new fetch.

`--snapshot <name>` fetches an older one. The current CI artifact path stays, behind
`--run <id>`, for pull requests and debugging.

## Publishing

A `release` job in `ci.yml`, after `boot`, `reproducible` and `cve-scan` (waited for,
not required), on `main` — by ref, not by event: auto-merge's commits arrive by
`workflow_dispatch`, since a push with `GITHUB_TOKEN` starts no workflow. It runs in
`concurrency: release` so two runs never publish at once; GitHub keeps one pending job
per group and cancels the one it replaces, whose commit is older and would have skipped
anyway (`result` does not count that cancellation). Everywhere else, and on `main` when
dispatched with `release_dry_run`, it runs with `--dry-run` in a group of its own: every
read, the whole collection and the notes, no write. So a branch that breaks what the
release needs fails `result`, before `main` finds out. It calls `tools/release.py`
(Python: it is GitHub's API, JSON and a report), which can also run against a fork:

1. Compute the name from the commit's UTC committer date and this workflow's
   `run_number`. If a *published* release with this tag exists (a retried run), stop
   without changing it. Otherwise skip unless the checks under *Only forward* pass.
   A *draft* with this tag is a failed attempt's leftover — invisible, without a tag
   yet, not immutable — and is deleted in step 3; stopping on it would wedge every
   retry.
2. Collect all required assets from the run's artifacts under their release names, fail
   if any are missing, and write `SHA256SUMS`. The workbook is the one optional asset: a
   missing one is noted in the release notes.
3. Create a draft release with that tag on that commit, upload every asset, write the
   notes.
4. Publish it and mark it latest. Nothing is visible before this step.
5. Delete snapshots at least 37 days old, counted from publication, and their tags,
   never the latest, and only releases whose tag has the snapshot shape. A failure here
   is a warning: the next run tries again. With immutable releases on, check that
   deleting a release and its tag is still allowed; if GitHub refuses the tag, expiry
   leaves tags behind and says so.

The image is assembled with `KAETZLI_VERSION` set to the name, so the reproducibility
job sets it too.

Locally, against any run: `gh run download <id> -D artifacts`, then
`GITHUB_RUN_NUMBER=<n> ./tools/release.py publish --dry-run --sha <sha>` from a
checkout of that commit (the image tags in the notes are the checkout's).
