#!/bin/bash
# Rebuild rootfs/ and extensions/ from every package's own staging tree:
#
#     ./tools/merge-staging.sh
#
# Each package installs into staging/<pkg>/, laid out like its CI artifact: rootfs/...
# for the base, extensions/<name>/... for a package with EXTENSION=. rootfs/ is derived,
# by this script, which root build.sh runs after every package and CI's rootfs job runs
# after unpacking the artifacts — so a rebuilt package replaces its files, and two
# packages installing one path fail here and in CI alike, in sorted order either way.
#
# The copies are --reflink=auto: on btrfs or XFS a copy-on-write clone that costs nothing,
# on ext4 a real copy. Not hardlinks, which would be as fast everywhere but would make an
# in-place edit of a file in rootfs/ an edit of the package's staging tree too.
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

STAGING=staging
STAMP=$STAGING/.merged

[ -d "$STAGING" ] || { echo "error: no $STAGING/ — build a package first (./build.sh glibc)" >&2; exit 1; }

# A rootfs/ this script did not make is a checkout from before staging/ existed, holding
# hours of builds that are in no staging tree. Deleting it to merge two packages would
# leave an image of two packages, so say what happened instead.
if [ ! -e "$STAMP" ] && [ -n "$(ls -A rootfs extensions 2>/dev/null)" ]; then
    echo "error: rootfs/ or extensions/ exists but was not made by $0." >&2
    echo "       Packages now install into staging/<pkg>/ and rootfs/ is merged from them" >&2
    echo "       (CLAUDE.md, 'How a package build works'). An older tree has no record of" >&2
    echo "       which package installed what, so it cannot be split; rebuild instead:" >&2
    echo "           ./tools/clean.sh && ./tools/build-image.sh" >&2
    exit 1
fi

# Files the container's users created are subuids from out here; podman unshare is the
# same fallback tools/clean.sh uses.
as_owner() { "$@" 2>/dev/null || podman unshare "$@"; }

trees=$(find "$STAGING" -mindepth 1 -maxdepth 1 -type d ! -name '.*' | sort)

# Every tree is a package this checkout defines. staging/ outlives a branch switch, so a
# package built on another branch would otherwise be merged into this one's image, SBOM
# and tests, which CI (artifacts of this commit only) would never see.
orphans=()
for t in $trees; do
    [ -f "packages/$(basename "$t")/env.sh" ] || orphans+=("$(basename "$t")")
done
if [ ${#orphans[@]} -gt 0 ]; then
    echo "error: staging/ holds packages this checkout does not define (another branch's?):" >&2
    printf '    %s\n' "${orphans[@]}" >&2
    echo "Remove them, then merge again:" >&2
    echo "    rm -rf$(printf ' staging/%s' "${orphans[@]}") && ./tools/merge-staging.sh" >&2
    exit 1
fi

# A staging tree holds rootfs/, extensions/ or both, and nothing else: anything beside
# them would be copied to the top of the checkout by the merge below.
for t in $trees; do
    extra=$(find "$t" -mindepth 1 -maxdepth 1 ! -name rootfs ! -name extensions)
    if [ -n "$extra" ]; then
        echo "error: $t holds more than rootfs/ and extensions/:" >&2
        echo "$extra" | sed 's/^/    /' >&2
        exit 1
    fi
done

# Every path two trees both install. Directories are shared by design, and so are the
# merged-/usr symlinks (bin -> usr/bin and friends), which builder/build-package.sh
# creates identically in every tree and extension; anything else is a real collision.
# e2fsprogs and util-linux both shipped /usr/bin/fsck until this existed in CI.
shared_links='^(rootfs|extensions/[^/]+)/(bin|lib|lib64|sbin|usr/sbin)'
listing=$(for t in $trees; do
              as_owner find "$t" -mindepth 1 ! -type d -printf "%P\t$(basename "$t")\n"
          done | LC_ALL=C sort)
dups=$(awk -F'\t' '{ n[$1]++; p[$1] = p[$1] " " $2 }
                   END { for (f in n) if (n[f] > 1) print f ":" p[f] }' <<< "$listing" | sort)
dups=$(grep -vE "$shared_links:" <<< "$dups" || true)
if [ -n "$dups" ]; then
    echo "error: more than one package installs these files:" >&2
    echo "$dups" | sed 's/^/    /' >&2
    echo "Fix it in one of the packages (e2fsprogs is built --disable-fsck for this)," >&2
    echo "never by ordering: the image would depend on which was copied last." >&2
    exit 1
fi

as_owner rm -rf rootfs extensions
# In sorted order, so the only things more than one tree provides — directories, whose
# modes then come from the last tree to have them, and the identical merged-/usr links —
# land the same way on every machine.
for t in $trees; do
    as_owner cp -a --reflink=auto "$t"/. ./
done
mkdir -p rootfs

# Which package installed each path of rootfs/, for image/build-rootfs.sh: a package the
# assembly removes whole (the container flavour has no kernel) leaves the SBOM with it,
# rather than its CVEs being reported against an image that does not have it. Not
# shipped; the assembly deletes it with the component records.
mkdir -p rootfs/usr/share/kaetzli
grep -vE "$shared_links"$'\t' <<< "$listing" | sed -n 's|^rootfs/||p' \
    > rootfs/usr/share/kaetzli/owners
touch "$STAMP"
