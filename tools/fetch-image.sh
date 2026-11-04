#!/usr/bin/env bash
# Download a bootable combination of the latest rootfs image + kernel into
# output/boot-image/, with the system extensions built in the same run beside them.
#
# Both come out of the same CI run, because they have to match: the kernel is a package
# in this repo (kernel/), and its container.config fragment is what makes the image able
# to run containers at all. Pulling a kernel from anywhere else gives you a guest that
# boots and then quietly has no `memory` cgroup controller, no overlayfs and no veth.
#
# By default it grabs the latest successful CI run on main that built an image (skipping
# runs in progress, docs-only runs and [skip ci] commits), for the host's own architecture.
# Override any of:
#   ARCH         amd64 or arm64 (default the host's own architecture)
#   ROOTFS_RUN   pin a specific kaetzli run id
#   REQUIRE_ATTESTATION=1  refuse a manifest without a signed attestation (only runs on
#                main have one; see below)
#   OUT          output directory (default output/boot-image, or output/boot-image-$ARCH
#                when ARCH is overridden away from the host's own)
#
# Nothing reaches $OUT unverified. The rootfs job of every CI run writes a manifest,
# bundle-<arch>/bundle.txt, naming the run, the commit and the arch, and the SHA-256 of
# every file in the three artifacts a boot needs (format below). This downloads that first,
# then the artifacts, and requires the two to agree exactly: same run, same commit as
# GitHub reports for it, every file listed present and matching, nothing unlisted. Only
# then is the bzImage taken out of the kernel's tar (after its member names are checked),
# the whole bundle assembled in a new directory beside $OUT, and the two swapped, so a
# failed or refused fetch leaves the previous bundle as it was. A run from before the
# manifest existed is refused; pin a newer one.
#
# bundle.txt, one record per line, `#` comments (written in ci.yml's rootfs job):
#   run <id>
#   commit <sha>
#   arch <amd64|arm64>
#   sha256 <hex> <artifact-name>/<path inside the artifact>     one per file
#
# The manifest proves the files are the ones that run produced and belong together. By
# itself it is not a signature: it is fetched over the same authenticated gh connection
# as the files. For a run on main it is signed too — the publish-oci job attests it — and
# that is checked with `gh attestation verify` against this repository's ci.yml and the
# run's commit.
#
# Examples:
#   ./tools/fetch-image.sh
#   ARCH=arm64 ./tools/fetch-image.sh
#   ROOTFS_RUN=29703135782 ./tools/fetch-image.sh
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

REPO="fwilhe2/kaetzli"

# Defaults to the host's own architecture. ci.yml builds and boot-tests both amd64 and
# arm64 in every run, tagging each artifact with the arch that produced it, so this has
# to pick one. Both uname's spelling (x86_64/aarch64) and this repo's (amd64/arm64) are
# accepted.
ARCH=$(normalize_arch "${ARCH:-}")

# Only suffix the default output directory when ARCH was overridden away from the
# host's own, so the common case — fetch, then boot, both with no ARCH set — still lands
# in plain output/boot-image on both sides.
host_arch=$(normalize_arch)
if [ "$ARCH" = "$host_arch" ]; then
    OUT="${OUT:-output/boot-image}"
else
    OUT="${OUT:-output/boot-image-$ARCH}"
fi

command -v gh >/dev/null || { echo "error: gh CLI not found" >&2; exit 1; }

die() { echo "error: $*" >&2; exit 1; }

# sha256sum on Linux, shasum on macOS, where boot-vz.sh users run this: bash 3.2, BSD tools.
sha256() {
    if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1
}

# The directory is swapped whole, so it may only hold what a bundle consists of; anything
# else in it would vanish with the swap.
if [ -d "$OUT" ]; then
    for f in "$OUT"/* "$OUT"/.[!.]*; do
        [ -e "$f" ] || [ -L "$f" ] || continue
        case "${f##*/}" in
            rootfs.erofs|rootfs.cmdline|bzImage|extensions|confexts|ci-run|bundle.txt) ;;
            *) die "$OUT holds ${f##*/}, which is not part of an image bundle; set OUT to another directory" ;;
        esac
    done
elif [ -e "$OUT" ]; then
    die "$OUT exists and is not a directory"
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# The newest successful run on main that built an image. Not simply the newest run:
# one still in progress has no artifacts yet, a docs-only push succeeds without building
# (ci.yml's `changes` job skips it), and a [skip ci] commit has no run at all. So walk
# back through main's finished, successful runs to the first with a bundle-$ARCH
# manifest that has not expired.
latest_bundle_run() {
    local id n
    for id in $(gh run list -R "$REPO" --workflow ci.yml --branch main --status success \
                    -L 50 --json databaseId -q '.[].databaseId'); do
        n=$(gh api "repos/$REPO/actions/runs/$id/artifacts?name=bundle-$ARCH" \
                -q '[.artifacts[] | select(.expired | not)] | length') || continue
        if [ "$n" -gt 0 ]; then echo "$id"; return; fi
    done
}
RUN="${ROOTFS_RUN:-$(latest_bundle_run)}"
[ -n "$RUN" ] || die "no successful run on main with a bundle-$ARCH artifact among the last 50"
case "$RUN" in *[!0-9]*) die "ROOTFS_RUN '$RUN' is not a run id" ;; esac
COMMIT=$(gh run view "$RUN" -R "$REPO" --json headSha -q .headSha)
[ -n "$COMMIT" ] || die "could not read the commit of run $RUN"

rootfs_art="rootfs.erofs-$ARCH" kernel_art="package-kernel-$ARCH" ext_art="extensions-$ARCH"

echo ">> manifest ($ARCH): $REPO run $RUN, commit $COMMIT (artifact: bundle-$ARCH)"
gh run download "$RUN" -R "$REPO" -n "bundle-$ARCH" -D "$tmp/manifest" ||
    die "run $RUN has no bundle-$ARCH manifest (a run from before fetch-image.sh verified downloads?); pin a newer ROOTFS_RUN"
manifest="$tmp/manifest/bundle.txt"
[ -f "$manifest" ] && [ ! -L "$manifest" ] || die "bundle-$ARCH has no bundle.txt"

# Parsed strictly: every line known, every path inside one of the three artifacts, no
# absolute path and no `..`, since these name files under $tmp.
m_run= m_commit= m_arch= have_ext=0
: > "$tmp/listed"
while read -r kind a b rest; do
    case "$kind" in
        ''|'#'*) continue ;;
        run)    m_run=$a ;;
        commit) m_commit=$a ;;
        arch)   m_arch=$a ;;
        sha256)
            [ -n "$b" ] && [ -z "$rest" ] || die "malformed manifest line: $kind $a $b $rest"
            case "$a" in *[!0-9a-f]*) die "malformed digest in the manifest: $a" ;; esac
            [ ${#a} -eq 64 ] || die "malformed digest in the manifest: $a"
            case "$b" in
                /*|*/../*|../*|*/..|*//*|*[!A-Za-z0-9._/-]*) die "refusing manifest path: $b" ;;
                "$rootfs_art"/*|"$kernel_art"/*) ;;
                "$ext_art"/*) have_ext=1 ;;
                *) die "manifest names a file outside this arch's artifacts: $b" ;;
            esac
            printf '%s %s\n' "$b" "$a" >> "$tmp/listed" ;;
        *) die "unknown manifest record: $kind" ;;
    esac
done < "$manifest"
[ "$m_run" = "$RUN" ]       || die "the manifest is for run '$m_run', not $RUN"
[ "$m_commit" = "$COMMIT" ] || die "the manifest is for commit '$m_commit', but run $RUN built $COMMIT"
[ "$m_arch" = "$ARCH" ]     || die "the manifest is for '$m_arch', not $ARCH"

# The manifest's signature, where it has one: ci.yml's publish-oci job attests every
# bundle.txt of a run on main (actions/attest, Sigstore), and the manifest names every
# other file's digest. Runs off main, and runs from before attestation began, have none;
# that is a note, unless REQUIRE_ATTESTATION=1 makes it a refusal.
if gh attestation verify "$manifest" -R "$REPO" --signer-workflow "$REPO/.github/workflows/ci.yml" \
        --source-digest "$COMMIT" --deny-self-hosted-runners > "$tmp/attestation.log" 2>&1; then
    echo "   signed: built by $REPO's ci.yml from $COMMIT (gh attestation verify)"
elif [ "${REQUIRE_ATTESTATION:-0}" = 1 ]; then
    sed 's/^/   /' "$tmp/attestation.log" >&2
    die "the manifest has no attestation that verifies, and REQUIRE_ATTESTATION=1"
else
    echo "   note: no attestation verifies this manifest (runs off main, and older ones, have"
    echo "         none); checked against the run's own artifacts only. REQUIRE_ATTESTATION=1 refuses."
fi
for need in "$rootfs_art/rootfs.erofs" "$rootfs_art/rootfs.cmdline" "$kernel_art/kernel.tar"; do
    grep -q "^$need " "$tmp/listed" || die "the manifest does not list $need"
done

mkdir "$tmp/art"
echo ">> rootfs ($ARCH): artifact $rootfs_art"
gh run download "$RUN" -R "$REPO" -n "$rootfs_art" -D "$tmp/art/$rootfs_art"
# The kernel package artifact rather than the assembled rootfs one: it is a tar, so the
# bzImage comes out byte-for-byte, and it is the same artifact the CI boot job uses.
echo ">> kernel ($ARCH): artifact $kernel_art"
gh run download "$RUN" -R "$REPO" -n "$kernel_art" -D "$tmp/art/$kernel_art"
# The system extensions built in the same run (docs/guides/sysext.md), when the manifest
# says there are any — required then, so an image never lands beside another run's.
if [ "$have_ext" = 1 ]; then
    echo ">> extensions ($ARCH): artifact $ext_art"
    gh run download "$RUN" -R "$REPO" -n "$ext_art" -D "$tmp/art/$ext_art"
else
    echo "   (no extensions in this run)"
fi

echo ">> verifying against the manifest"
if [ -n "$(find "$tmp/art" ! -type f ! -type d)" ]; then
    die "the downloads contain something other than plain files: $(find "$tmp/art" ! -type f ! -type d)"
fi
(cd "$tmp/art" && find . -type f | sed 's|^\./||' | LC_ALL=C sort) > "$tmp/got"
cut -d' ' -f1 "$tmp/listed" | LC_ALL=C sort > "$tmp/want"
if ! cmp -s "$tmp/got" "$tmp/want"; then
    echo "error: the downloaded files and the manifest disagree (< unlisted, > missing):" >&2
    diff "$tmp/got" "$tmp/want" | grep '^[<>]' >&2 || true
    exit 1
fi
while read -r path digest; do
    [ "$(sha256 "$tmp/art/$path")" = "$digest" ] || die "checksum mismatch: $path"
done < "$tmp/listed"
echo "   $(wc -l < "$tmp/listed" | tr -d ' ') files match"

# The tar is verified; its member names are still checked before anything is extracted,
# and the one member taken has to be a plain file.
kernel_tar="$tmp/art/$kernel_art/kernel.tar"
tar tf "$kernel_tar" > "$tmp/members" || die "$kernel_art/kernel.tar is not a readable tar"
bad=$(grep -E '^/|(^|/)\.\.(/|$)' "$tmp/members" || true)
[ -z "$bad" ] || die "kernel.tar has unsafe member names: $bad"
grep -qx 'rootfs/boot/bzImage' "$tmp/members" || die "kernel.tar has no rootfs/boot/bzImage"
case "$(tar tvf "$kernel_tar" rootfs/boot/bzImage | head -n1)" in
    -*) ;;
    *) die "rootfs/boot/bzImage in kernel.tar is not a regular file" ;;
esac
mkdir "$tmp/kernel"
tar xf "$kernel_tar" -C "$tmp/kernel" rootfs/boot/bzImage
[ -f "$tmp/kernel/rootfs/boot/bzImage" ] && [ ! -L "$tmp/kernel/rootfs/boot/bzImage" ] ||
    die "extracting the bzImage did not produce a regular file"

# The new bundle, complete, in a directory beside $OUT (the same filesystem, so the
# swap is two renames): the image and the kernel arguments that carry its dm-verity
# root hash, the kernel, the extensions, the manifest, and the run they came from
# (tools/vm.sh shows it).
parent=$(dirname "$OUT")
mkdir -p "$parent"
new=$(mktemp -d "$parent/.$(basename "$OUT").new.XXXXXX")
trap 'rm -rf "$tmp" "$new"' EXIT
cp "$tmp/art/$rootfs_art/rootfs.erofs" "$tmp/art/$rootfs_art/rootfs.cmdline" "$new/"
cp "$tmp/kernel/rootfs/boot/bzImage" "$new/bzImage"
if [ "$have_ext" = 1 ]; then
    cp -R "$tmp/art/$ext_art"/. "$new/"
fi
cp "$manifest" "$new/bundle.txt"
echo "$RUN" > "$new/ci-run"
chmod 755 "$new"

old=
if [ -e "$OUT" ]; then
    old="$parent/.$(basename "$OUT").old.$$"
    mv "$OUT" "$old"
fi
if ! mv "$new" "$OUT"; then
    [ -z "$old" ] || mv "$old" "$OUT"
    die "could not move the new bundle into $OUT; the previous one is unchanged"
fi
[ -z "$old" ] || rm -rf "$old"

echo
echo "Downloaded and verified into $OUT/ (run $RUN, commit $COMMIT):"
ls -la "$OUT/rootfs.erofs" "$OUT/rootfs.cmdline" "$OUT/bzImage" "$OUT"/extensions/*.raw "$OUT"/confexts/*.raw 2>/dev/null || true
if [ "$ARCH" = "$host_arch" ]; then
    echo "Boot it with: ./tools/vm.sh start, or ./tools/boot-qemu.sh"
else
    echo "Boot it with: ARCH=$ARCH ./tools/boot-qemu.sh"
fi
