#!/usr/bin/env bash
# Move one package to a new upstream version:
#
#     ./tools/bump-version.sh kernel 7.1.7
#
# Two lines of env.sh change, not one. PACKAGE, TARBALL and URL are all derived from
# VERSION, but SHA256 cannot be — it is a hash of bytes that exist only upstream — so it
# has to be fetched and computed. A bump that rewrites VERSION alone leaves the previous
# release's checksum sitting there, and tools/fetch-sources.sh then refuses the new
# tarball:
#
#     kernel: CHECKSUM MISMATCH from https://cdn.kernel.org/.../linux-7.1.7.tar.xz
#            expected 995dd7188d924662...   <- 7.1.6's
#            got      ca8f2a6884a4d620...
#
# which is the guard doing its job, and a build that never starts.
# .github/workflows/update-packages.yml calls this; that is the reason it is a script
# rather than two lines of sed in the workflow.
#
# For a package whose upstream signs its releases (SIG_URL in env.sh), the new tarball
# must verify against packages/<pkg>/upstream-key.asc (tools/verify-signature.sh) before
# the bump stands. Otherwise it records what the canonical URL served at the moment of
# the bump — trust on first use.
# The pin earns its keep afterwards: mirrors, the vendored sources image and every later
# rebuild are all held to exactly those bytes. For the same reason only $URL is tried and
# never $MIRRORS — a mirror serving something else is precisely what the checksum is for,
# and pinning a mirror's bytes would enshrine the problem instead of catching it.
#
#     ./tools/bump-version.sh --vendor tcpdump 1.11.0   # the second tarball's VENDOR_VERSION
#
# Our own patches (packages/<pkg>/patches/*.patch) are left alone: one that no longer
# applies fails the build in the pull request, which is where it has to be looked at.
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

vendor=
[ "${1:-}" = --vendor ] && { vendor=1; shift; }

PKG="${1:-}"
NEW="${2:-}"

if [ -z "$PKG" ] || [ -z "$NEW" ]; then
    echo "usage: ${0##*/} [--vendor] <package> <version>" >&2
    exit 2
fi

PKG=$(package_name "$PKG")
env_file="packages/$PKG/env.sh"

# --vendor: the second tarball's own VENDOR_VERSION (tcpdump's libpcap), and its checksum.
# Allowed for a FOLLOWS package too — linux-tools' libtraceevent is its own, whatever
# kernel it follows.
if [ -n "$vendor" ]; then
    old_version=$(sed -n -E 's|^VENDOR_VERSION="?([^"]*)"?.*|\1|p' "$env_file" | head -n1)
    [ -n "$old_version" ] || { echo "error: $env_file sets no VENDOR_VERSION" >&2; exit 1; }
    sed -i -E "s|^VENDOR_VERSION=.*|VENDOR_VERSION=\"$NEW\"|" "$env_file"
    mapfile -t vendor_values < <(env_get "$PKG" VENDOR_TARBALL VENDOR_URL)
    mkdir -p downloads
    echo "$PKG: fetching ${vendor_values[1]}"
    rm -f "downloads/${vendor_values[0]}"
    "${CURL_DOWNLOAD[@]}" -o "downloads/${vendor_values[0]}" "${vendor_values[1]}"
    vendor_sha=$(sha256sum <"downloads/${vendor_values[0]}" | cut -d' ' -f1)
    sed -i -E "s|^VENDOR_SHA256=.*|VENDOR_SHA256=\"$vendor_sha\"|" "$env_file"
    echo "$PKG: vendor $old_version -> $NEW"
    echo "  ${vendor_values[0]}"
    echo "  sha256 -> $vendor_sha"
    ./tools/fetch-sources.sh "$PKG" >/dev/null || { echo "error: $PKG does not verify after the bump" >&2; exit 1; }
    exit 0
fi

# linux-tools sources the kernel's env.sh for its VERSION, tarball and checksum, so it has
# none of its own to rewrite — bumping the package it follows moves it too.
follows=$(env_get "$PKG" FOLLOWS)
if [ -n "$follows" ]; then
    echo "error: $PKG follows $follows; bump that instead: ${0##*/} $follows $NEW" >&2
    exit 1
fi

old_version=$(sed -n -E 's|^VERSION="?([^"]*)"?.*|\1|p' "$env_file" | head -n1)
old_sha=$(sed -n -E 's|^SHA256="?([^"]*)"?.*|\1|p' "$env_file" | head -n1)

sed -i -E "s|^VERSION=.*|VERSION=\"$NEW\"|" "$env_file"

# Read the file back rather than composing the URL here: every package spells its own,
# some of them out of $PKG, and this has to be the same string fetch-sources.sh will use.
#
# mapfile rather than `IFS=$'\t' read`, because a tab is IFS *whitespace*: bash collapses
# runs of it and drops leading empties, so a LOCAL_SOURCE package — which has neither a
# TARBALL nor a URL — would hand its "1" to the first variable and read as an ordinary
# package with no URL set.
mapfile -t env_values < <(env_get "$PKG" TARBALL URL LOCAL_SOURCE)
tarball="${env_values[0]:-}"
url="${env_values[1]:-}"
local_source="${env_values[2]:-}"

if [ -n "$local_source" ]; then
    echo "$PKG: $old_version -> $NEW (source is in this repository; no tarball, no checksum)"
    exit 0
fi

[ -n "$url" ] || { echo "error: $env_file sets no URL" >&2; exit 1; }

[ -n "$tarball" ] || { echo "error: $env_file sets no TARBALL" >&2; exit 1; }

# Into downloads/ rather than a temporary file, so the verification at the bottom costs
# nothing: fetch-sources.sh finds it already there and only hashes it. A wrong or
# half-written file left behind by a failure here is not a trap either — that is exactly
# the case fetch-sources.sh refetches.
mkdir -p downloads
echo "$PKG: fetching $url"
# Same retries as tools/fetch-sources.sh, minus its --continue-at: this deliberately
# starts from nothing, and resuming onto a previous version's leftovers is how a
# download of the wrong size gets a plausible name.
rm -f "downloads/$tarball"
"${CURL_DOWNLOAD[@]}" -o "downloads/$tarball" "$url"

new_sha=$(sha256sum <"downloads/$tarball" | cut -d' ' -f1)

if grep -q '^SHA256=' "$env_file"; then
    sed -i -E "s|^SHA256=.*|SHA256=\"$new_sha\"|" "$env_file"
else
    # Keep it with the three lines it belongs beside rather than at the end of the file.
    sed -i -E "/^URL=/a SHA256=\"$new_sha\"" "$env_file"
fi

echo "$PKG: $old_version -> $NEW"
echo "  $tarball"
echo "  sha256 ${old_sha:-(none)} -> $new_sha"

# And the second tarball, for a package that has one: the vendored crates of a Rust
# package are versioned with it, so a bump that rewrote only SHA256 would leave the
# previous release's VENDOR_SHA256 behind and fail in fetch-sources.sh exactly the way
# the note at the top of this file describes for the first.
mapfile -t vendor_values < <(env_get "$PKG" VENDOR_TARBALL VENDOR_URL)
vendor_tarball="${vendor_values[0]:-}"
vendor_url="${vendor_values[1]:-}"
if [ -n "$vendor_tarball" ]; then
    echo "$PKG: fetching $vendor_url"
    rm -f "downloads/$vendor_tarball"
    "${CURL_DOWNLOAD[@]}" -o "downloads/$vendor_tarball" "$vendor_url"
    vendor_sha=$(sha256sum <"downloads/$vendor_tarball" | cut -d' ' -f1)
    sed -i -E "s|^VENDOR_SHA256=.*|VENDOR_SHA256=\"$vendor_sha\"|" "$env_file"
    echo "  $vendor_tarball"
    echo "  sha256 -> $vendor_sha"
fi

# Free, now that the tarball is already in downloads/, and worth doing: it proves the
# file that was just written describes the bytes it names, read back through the same
# code that will reject it in CI if it does not.
if ! ./tools/fetch-sources.sh "$PKG" >/dev/null; then
    echo "error: $PKG does not verify after the bump" >&2
    exit 1
fi

# The checksum just written is only what the URL served. Where upstream signs releases
# (SIG_URL), those bytes must also verify against a key pinned in git, or the bump stops
# here and no pull request is opened: a key nobody pinned is a person's decision
# (tools/verify-signature.sh says how).
if [ -n "$(env_get "$PKG" SIG_URL)" ] && ! ./tools/verify-signature.sh "$PKG"; then
    echo "error: $PKG $NEW is not signed by a pinned key; not bumping" >&2
    exit 1
fi

# A package whose fixes come as an upstream patch series (bash) or release branch (glibc)
# starts a new one with every release: the old version's patches go, the new one's —
# often none yet — come.
if [ -n "$(env_get "$PKG" UPSTREAM_PATCHES UPSTREAM_PATCH_GIT | tr -d '\n')" ]; then
    ./tools/sync-patches.sh --new-series "$PKG"
fi
