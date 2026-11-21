#!/usr/bin/env bash
# The CVEs a package's patches fix, one per line:
#
#     ./tools/patch-cves.sh glibc
#     CVE-2026-8674	upstream/0019-resolv-Fix-assertion-failure-on-search-list-trunc.patch	git+https://sourceware.org/git/glibc.git@1f502624…
#
# Tab-separated: the CVE, the patch under packages/<pkg>/patches/, and where the fix comes
# from (the upstream commit — for our own patches too, which are upstream's commits verbatim —
# or the upstream patch file).
# build.sh records these in the package's component record, image/sbom.py turns them into
# SPDX `fix` references, and test/cve-scan.py into an OpenVEX document grype applies, so
# a CVE a patch closes is reported as patched rather than open (docs/guides/patches.md).
#
# Two sources, because neither is complete:
#
#   - the patch's message: a CVE named anywhere before the diff starts. That is the commit
#     message of a `git format-patch` file (glibc's backports say "CVE-2026-8674" in the
#     subject or body), the report heading a bash53-NNN, or a line in our own patch. The
#     diff itself is never read: glibc's first backport adds a file listing every advisory
#     ever published, and none of those is fixed by it.
#   - patches/upstream.cves, which tools/sync-patches.sh writes from an upstream's advisory
#     database where there is one (glibc's advisories/, by Fix-Commit on our branch): it
#     knows fixes whose commit message does not name the CVE.
#
# Our own patch says what it fixes in its message, e.g. a `CVE: CVE-2026-12345` line
# above the diff. A message that names a CVE it does not fix ("the fix for CVE-x did not
# cover…") would be counted, so read what this prints when a new patch lands.
#
# Where upstream marks its fixes with a trailer, env.sh says so and only that counts:
# UPSTREAM_PATCH_CVE_TRAILER="Fixes" reads `Fixes: CVE-2026-88823` and nothing else
# (OpenSSL's release branch also has "fix typos in CVE-…" and "Add test for CVE-…").
# It applies to upstream/ only; our own patches keep the `CVE:` line.
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

[ -n "${1:-}" ] || { echo "usage: ${0##*/} <package>" >&2; exit 2; }
PKG=$(package_name "$1")
dir="packages/$PKG/patches"
mapfile -t v < <(env_get "$PKG" UPSTREAM_PATCH_GIT UPSTREAM_PATCHES UPSTREAM_GITHUB UPSTREAM_PATCH_CVE_TRAILER)
repo=${v[0]:-} index=${v[1]:-} github=${v[2]:-} trailer=${v[3]:-}
# Our own patches are upstream's commits verbatim (docs/guides/patches.md), so their
# fix is that commit too, in the repository the release branch comes from or else the
# GitHub project releases are taken from.
own_repo=$repo
[ -n "$own_repo" ] || [ -z "$github" ] || own_repo="https://github.com/$github"

# locator <patch> — where the fix in that patch comes from. image/sbom.py makes a URL of
# git+<repo>@<sha>; anything it cannot map, test/check-sbom.py fails, naming the form.
locator() {
    local f=$1 sha
    sha=$(sed -n '1s/^From \([0-9a-f]\{40\}\) .*/\1/p' "$f")
    case $f in
        "$dir"/upstream/*)
            if [ -n "$repo" ]; then
                echo "git+$repo@$sha"
            else
                echo "${index%/}/${f##*/}"
            fi ;;
        *)
            if [ -n "$own_repo" ] && [ -n "$sha" ]; then
                echo "git+$own_repo@$sha"
            else
                echo "git+https://github.com/fwilhe2/kaetzli#$f"
            fi ;;
    esac
}

{
    while read -r f; do
        [ -n "$f" ] || continue
        # Everything before the first line that starts a diff: format-patch's `---`, a
        # unified or context diff header, or `Index:`.
        only=.
        case $f in "$dir"/upstream/*) [ -z "$trailer" ] || only="^$trailer:[[:space:]]+CVE-" ;; esac
        awk '/^(---$|--- |\*\*\* |diff |Index: )/ { exit } { print }' "$f" |
            { grep -E "$only" || true; } |
            { grep -oE 'CVE-[0-9]{4}-[0-9]{4,}' || true; } | sort -u |
            while read -r cve; do printf '%s\t%s\t%s\n' "$cve" "${f#"$dir"/}" "$(locator "$f")"; done
    done < <(package_patches "$PKG")

    # <cve> <commit> <advisory>, by commit: the patch whose From line names it.
    if [ -f "$dir/upstream.cves" ]; then
        while read -r cve sha _; do
            f=$(grep -l "^From $sha " "$dir"/upstream/* 2>/dev/null | head -n1) || true
            [ -n "$f" ] || continue
            printf '%s\t%s\t%s\n' "$cve" "${f#"$dir"/}" "$(locator "$f")"
        done < "$dir/upstream.cves"
    fi
} | LC_ALL=C sort -u -t$'\t' -k1,1
