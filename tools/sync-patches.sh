#!/usr/bin/env bash
# Make packages/<pkg>/patches/upstream/ exactly the patch series upstream publishes for the
# pinned VERSION — as files on a server, or as a release branch in git:
#
#     ./tools/sync-patches.sh bash          # download what is missing, drop what is gone
#     ./tools/sync-patches.sh glibc         # release/2.44/master since glibc-2.44
#     ./tools/sync-patches.sh --list bash   # print upstream's series, change nothing
#
# For a project that ships fixes as patches against a release rather than as a new
# release — GNU bash's bash53-001, -002, … — the release tarball alone is not the current
# upstream. env.sh says where the series is:
#
#   UPSTREAM_PATCHES       URL of the directory listing the patches
#   UPSTREAM_PATCH_REGEX   ERE matching one patch's file name (and not its .sig)
#   UPSTREAM_PATCH_STRIP   the -p level they apply with (default 1; bash's are -p0)
#   UPSTREAM_PATCH_SIG     the signature's suffix, with upstream-key.asc (default .sig)
#   UPSTREAM_PATCH_SKIP    ERE of names that match but are in the tarball already
#
# A name ending .gz is verified compressed, as it is signed, and committed decompressed.
#
# Or a release branch in git, for a project whose fixes are backported there and never
# released as a tarball until the next point release — glibc's release/2.44/master:
#
#   UPSTREAM_PATCH_GIT     the repository
#   UPSTREAM_PATCH_BRANCH  the branch to follow
#   UPSTREAM_PATCH_BASE    the tag the tarball is (glibc-2.44)
#   UPSTREAM_PATCH_ADVISORIES  <branch>:<dir> of glibc-format advisories (CVE-Id:,
#                          Fix-Commit: <sha> (<version>-<n>)), optional: written to
#                          patches/upstream.cves for tools/patch-cves.sh
#
# The series is then `git format-patch BASE..BRANCH`: the tarball plus these patches is
# the branch's tree at its head, which each patch's `From <sha>` line names. Only the
# commits since the tag are fetched (--shallow-exclude, then one deeper for the tag
# itself), so this does not clone the repository. --list compares the branch head with
# the last patch's commit first, and fetches only when they differ.
#
# A sync only appends (see replace_series), and with packages/<pkg>/upstream-key.asc every
# new patch file must carry a good signature from that pinned key; either failure is exit
# 3, and the update workflow opens nothing. --new-series lifts the first, for a release.
#
# The patches are committed, so a build never needs the network for them, and they go
# into the sources image beside the tarballs (tools/prep.sh). Like tools/bump-version.sh,
# this trusts what upstream serves at the moment of the sync; git is the pin afterwards,
# and a series that changed under us shows up as a diff of a file already committed.
#
# tools/check-updates.py proposes a sync when upstream has more patches than we do, the
# update workflow runs this to make the pull request, and tools/bump-version.sh runs it
# after every version bump of such a package — a new release starts a new series.
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

list_only= new_series=
case "${1:-}" in
    --list) list_only=1; shift ;;
    --new-series) new_series=1; shift ;;
esac
[ -n "${1:-}" ] || { echo "usage: ${0##*/} [--list|--new-series] <package>" >&2; exit 2; }

PKG=$(package_name "$1")
dest="packages/$PKG/patches/upstream"
mapfile -t v < <(env_get "$PKG" UPSTREAM_PATCHES UPSTREAM_PATCH_REGEX \
                 UPSTREAM_PATCH_GIT UPSTREAM_PATCH_BRANCH UPSTREAM_PATCH_BASE UPSTREAM_PATCH_ADVISORIES)
index=${v[0]:-} regex=${v[1]:-} repo=${v[2]:-} branch=${v[3]:-} base=${v[4]:-} advisories=${v[5]:-}

# replace_series <dir> — make $dest exactly the files in <dir>.
#
# Append-only, unless --new-series (tools/bump-version.sh, for a new release): every patch
# already committed must still be there, byte for byte, and every new one must sort after
# them. Upstream adds to a series; it does not edit one. A patch that changed or went is a
# rewritten branch, a replaced file or a compromised server, and none of those may reach
# main unattended — so this fails, the update workflow opens no pull request, and a human
# looks (exit 3).
replace_series() {
    if [ -z "$new_series" ] && [ -d "$dest" ]; then
        local f last=
        for f in "$dest"/*; do
            [ -f "$f" ] || continue
            if ! cmp -s "$f" "$1/${f##*/}"; then
                echo "error: $PKG: upstream changed or removed ${f##*/}, which is already committed;" >&2
                echo "       refusing to rewrite the series (a version bump uses --new-series)" >&2
                exit 3
            fi
            last=${f##*/}
        done
        for f in "$1"/*; do
            [ -e "$dest/${f##*/}" ] && continue
            if [ -n "$last" ] && [ "$(printf '%s\n%s\n' "$last" "${f##*/}" | LC_ALL=C sort | head -n1)" != "$last" ]; then
                echo "error: $PKG: new patch ${f##*/} sorts before ${last}, so it is not an addition at the end" >&2
                exit 3
            fi
        done
    fi
    rm -rf "$dest"
    if [ -n "$(ls -A "$1")" ]; then
        mkdir -p "$dest"
        cp "$1"/* "$dest/"
    fi
    echo "$PKG: $( { find "$dest" -type f 2>/dev/null || true; } | wc -l) upstream patches for $(env_get "$PKG" VERSION)${head:+, $branch at ${head:0:12}}"
}

if [ -n "$repo" ]; then
    if [ -z "$branch" ] || [ -z "$base" ]; then
        echo "error: packages/$PKG/env.sh sets UPSTREAM_PATCH_GIT without UPSTREAM_PATCH_BRANCH and _BASE" >&2
        exit 1
    fi
    head=$(git ls-remote "$repo" "refs/heads/$branch" | cut -f1)
    [ -n "$head" ] || { echo "error: $repo has no branch $branch" >&2; exit 1; }
    ours=$( { find "$dest" -type f 2>/dev/null || true; } | LC_ALL=C sort | tail -n1)
    if [ -n "$list_only" ] && [ -n "$ours" ] && [ "$(head -c 45 "$ours" | cut -c6-45)" = "$head" ]; then
        ls "$dest"
        exit 0
    fi

    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    git init -q "$tmp/git"
    g() { git -C "$tmp/git" "$@"; }
    g remote add origin "$repo"
    g fetch -q --shallow-exclude="$base" origin "+refs/heads/$branch:refs/remotes/upstream/branch"
    # One deeper, so the first commit after the tag has its parent: without it that
    # commit is a root, and format-patch writes it as the whole tree, created.
    g fetch -q --deepen=1 origin "+refs/heads/$branch:refs/remotes/upstream/branch"
    g fetch -q --depth=1 origin "+refs/tags/$base:refs/tags/$base"
    g merge-base --is-ancestor "$base" upstream/branch ||
        { echo "error: $base is not an ancestor of $branch" >&2; exit 1; }
    # Pinned diff settings, so the bytes depend on the commits and not on whoever ran this:
    # GNU patch cannot apply a rename or a binary diff, and abbrev/prefix change the text.
    # --no-numbered, because "[PATCH 01/66]" names the length of the series in every file,
    # so one new commit upstream would rewrite them all and fail the append-only check.
    # The file names still carry the order (0001-…).
    mkdir "$tmp/series"
    g -c core.abbrev=12 -c diff.renames=false -c diff.noprefix=false -c diff.mnemonicPrefix=false \
        -c diff.algorithm=myers -c format.signOff=false -c format.numbered=false \
        format-patch -q --no-numbered --no-signature --no-renames --no-binary \
        -o "$tmp/series" "$base..upstream/branch"
    if grep -l '^Binary files ' "$tmp/series"/* 2>/dev/null; then
        echo "error: a commit on $branch changes a binary file, which patch(1) cannot apply" >&2
        exit 1
    fi
    if [ -n "$list_only" ]; then
        ls "$tmp/series"
        exit 0
    fi
    replace_series "$tmp/series"

    # The CVEs upstream's advisories say a commit in the series fixes, for
    # tools/patch-cves.sh: glibc's advisories/GLIBC-SA-* on master carry `CVE-Id:` and a
    # `Fix-Commit: <sha> (2.44-31)` per release branch, which catches the fixes whose
    # commit message does not name the CVE. Only that directory is fetched: the newest
    # commit, no blobs, then a sparse checkout, which fetches its blobs in one request.
    cves="packages/$PKG/patches/upstream.cves"
    if [ -n "$advisories" ]; then
        g fetch -q --depth=1 --filter=blob:none origin "+refs/heads/${advisories%%:*}:refs/remotes/upstream/advisories"
        g sparse-checkout set --no-cone "/${advisories#*:}/" 2>/dev/null
        g checkout -q upstream/advisories
        series=$(sed -s -n '1s/^From \([0-9a-f]\{40\}\) .*/\1/p' "$dest"/*)
        version=$(env_get "$PKG" VERSION)
        for f in "$tmp/git/${advisories#*:}"/*; do
            [ -f "$f" ] || continue
            awk -v v="$version" -v sa="${f##*/}" '
                /^CVE-Id:/ { for (i = 2; i <= NF; i++) cve[++n] = $i }
                /^Fix-Commit:/ && (index($0, "(" v "-") || index($0, "(" v ")")) { fix[++m] = $2 }
                END { for (i = 1; i <= n; i++) for (j = 1; j <= m; j++) print cve[i], fix[j], sa }' "$f"
        done | grep -F -f <(printf '%s\n' "$series") | LC_ALL=C sort -u > "$cves" || true
        [ -s "$cves" ] || rm -f "$cves"
        echo "$PKG: $( { cat "$cves" 2>/dev/null || true; } | wc -l) fixes named by upstream's advisories"
    fi
    exit 0
fi

if [ -z "$index" ] || [ -z "$regex" ]; then
    echo "error: packages/$PKG/env.sh sets neither UPSTREAM_PATCHES/UPSTREAM_PATCH_REGEX nor UPSTREAM_PATCH_GIT" >&2
    exit 1
fi
index="${index%/}/"

# Bounded by quotes or '>' on both sides, so bash53-001.sig never matches as bash53-001.
# A listing that is missing entirely is a 404 and fails here; an existing listing with
# nothing in it is a release with no patches yet, which is normal.
page=$(fetch "$index")
names=$(printf '%s\n' "$page" | grep -oE "[\">]$regex[\"<]" | tr -d '"<>' | LC_ALL=C sort -u || true)
# UPSTREAM_PATCH_SKIP: names that match but are already in the tarball (ncurses' release
# is its 20251230 patch).
skip=$(env_get "$PKG" UPSTREAM_PATCH_SKIP)
if [ -n "$skip" ]; then
    names=$(printf '%s\n' "$names" | grep -Evx "$skip" || true)
fi

if [ -n "$list_only" ]; then
    [ -z "$names" ] || printf '%s\n' "$names"
    exit 0
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# packages/<pkg>/upstream-key.asc: the key upstream signs its patches with, pinned in git.
# When it is there, every patch fetched here must come with <name>.sig and verify against
# it — not against whatever key the server offers — or nothing is written. Patches already
# committed are not fetched again (they are compared, unchanged, above).
key="packages/$PKG/upstream-key.asc"
if [ -f "$key" ]; then
    command -v gpg >/dev/null && command -v gpgv >/dev/null ||
        { echo "error: $key is pinned, and verifying against it needs gpg and gpgv" >&2; exit 1; }
fi

sig=$(env_get "$PKG" UPSTREAM_PATCH_SIG)
sig=${sig:-.sig}
mkdir "$tmp/series"
while read -r name; do
    [ -n "$name" ] || continue
    # A compressed patch (ncurses' .patch.gz) is verified as it was signed, compressed, and
    # committed decompressed: the build applies it, and git diffs it.
    local_name=${name%.gz}
    if [ -f "$dest/$local_name" ]; then
        cp "$dest/$local_name" "$tmp/series/$local_name"
        continue
    fi
    echo "  $PKG: fetching $index$name"
    "${CURL_DOWNLOAD[@]}" -o "$tmp/$name" "$index$name"
    if [ -f "$key" ]; then
        [ -f "$tmp/keyring.gpg" ] || gpg --batch --quiet --dearmor < "$key" > "$tmp/keyring.gpg"
        "${CURL_DOWNLOAD[@]}" -o "$tmp/$name$sig" "$index$name$sig"
        if ! gpgv --quiet --keyring "$tmp/keyring.gpg" "$tmp/$name$sig" "$tmp/$name" 2>/dev/null; then
            echo "error: $PKG: $name does not verify against $key" >&2
            exit 3
        fi
        echo "  $PKG: $name: good signature from the pinned key"
    fi
    if [ "$name" != "$local_name" ]; then
        gzip -dc "$tmp/$name" > "$tmp/series/$local_name"
    else
        mv "$tmp/$name" "$tmp/series/$local_name"
    fi
done <<< "$names"

replace_series "$tmp/series"
