#!/usr/bin/env bash
# Put every package's source tarball in downloads/, verified against the checksum in its
# env.sh:
#
#     ./tools/fetch-sources.sh              # all packages
#     ./tools/fetch-sources.sh systemd xz   # just these
#
# build.sh calls this for the one package it is building. Run it with no arguments to
# fetch everything up front, which is the point: after it succeeds nothing else in a
# build needs the network, and the build container is run with --network=none to make
# that true rather than merely intended.
#
# A tarball is only ever accepted if its SHA256 matches. That is what makes a mirror
# safe to fall back to when upstream is unreachable, and what makes an already-present
# file safe to reuse without re-downloading.
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

mkdir -p downloads

# A transfer that dies at 80% is the normal failure of a bad link, so resume rather than
# start over — restarting the 150 MB kernel tarball is how a flaky connection turns into
# an infinite loop.
# A host that is down answers no SYN, and the kernel gives up only after two minutes —
# per attempt, six attempts per URL: an unreachable savannah once cost 27 minutes before
# the mirror was tried. Twenty seconds is far more than any live server needs.
CURL=("${CURL_DOWNLOAD[@]}" --continue-at - --connect-timeout 20)
# Before a mirror: two retries, two seconds apart (curl takes the last --retry given).
CURL_QUICK=("${CURL[@]}" --retry 2 --retry-delay 2)

# Run in a subshell (see the loop below), so one package's env.sh cannot leak into the
# next — an unset list kept by hand here used to be what prevented that.
fetch_one() {
    local pkg="$1"
    load_env "$pkg"

    # A package whose source is in this repository has no tarball and no checksum, so
    # there is nothing here to fetch or verify. It is still a package everywhere else.
    if [ -n "${LOCAL_SOURCE:-}" ]; then
        echo "  $pkg: source is in this repository, nothing to fetch"
        return 0
    fi

    fetch_file "$pkg" "$TARBALL" "$URL" "${SHA256:-}" "${MIRRORS:-}" || return 1

    # A second tarball, for the Rust packages whose crates come as a separate download
    # (netavark-<v>-vendor.tar.gz): pinned and verified exactly like the first, because
    # it is source too — every line of it is compiled into the binary.
    if [ -n "${VENDOR_TARBALL:-}" ]; then
        fetch_file "$pkg" "$VENDOR_TARBALL" "$VENDOR_URL" "${VENDOR_SHA256:-}" "" || return 1
    fi
}

# fetch_file <pkg> <tarball> <url> <sha256> <mirrors>
fetch_file() {
    local pkg=$1 tarball=$2 primary=$3 sha=$4 mirrors=$5
    local target="downloads/$tarball"

    if [ -z "$sha" ]; then
        echo "error: packages/$pkg/env.sh has no checksum for $tarball" >&2
        echo "       add one with: sha256sum $target" >&2
        return 1
    fi

    if [ -f "$target" ] && verify "$target" "$sha"; then
        echo "  $pkg: $tarball already present and verified"
        return 0
    fi

    # A file that is present but wrong is either a half-finished download (which curl
    # will resume onto, producing garbage) or a different file under the same name.
    # Neither is recoverable in place.
    [ -f "$target" ] && { echo "  $pkg: $tarball fails its checksum, refetching"; rm -f "$target"; }

    # A URL with another one after it gets a short retry, so a dead primary hands over
    # to its mirror in seconds; only the last waits out an outage with CURL's backoff.
    # shellcheck disable=SC2206 # MIRRORS is a space-separated list
    local url urls=("$primary" $mirrors) i
    for i in "${!urls[@]}"; do
        if [ "$i" -lt $(( ${#urls[@]} - 1 )) ]; then
            QUICK=1 try_url "$pkg" "$target" "$sha" "${urls[$i]}" && return 0
        else
            try_url "$pkg" "$target" "$sha" "${urls[$i]}" && return 0
        fi
    done

    # Last, and only now, because it costs an API request: see debian_snapshot_url.
    url=$(debian_snapshot_url "$primary")
    [ -n "$url" ] && try_url "$pkg" "$target" "$sha" "$url" && return 0

    echo "error: could not fetch $tarball for $pkg from any source" >&2
    return 1
}

# try_url <pkg> <target> <sha256> <url> — download and verify one candidate.
try_url() {
    local pkg=$1 target=$2 sha=$3 url=$4
    echo "  $pkg: fetching $url"
    if [ -n "${QUICK:-}" ]; then
        "${CURL_QUICK[@]}" -o "$target" "$url" || return 1
    else
        "${CURL[@]}" -o "$target" "$url" || return 1
    fi
    verify "$target" "$sha" && return 0
    # Not a transport problem: this URL serves the wrong bytes, and retrying it or
    # resuming onto it cannot help. Say so loudly and move to the next source.
    echo "  $pkg: CHECKSUM MISMATCH from $url" >&2
    echo "         expected $sha" >&2
    echo "         got      $(sha256sum <"$target" | cut -d' ' -f1)" >&2
    rm -f "$target"
    return 1
}

# debian_snapshot_url <url> — for a deb.debian.org pool URL, the same file on
# snapshot.debian.org; for anything else, nothing.
#
# The pool keeps a version only while some suite still names it, so a sid package is
# deleted the day its successor migrates. Even a non-maintainer upload is enough
# (ca-certificates 20260816 went that way under +nmu1). Snapshot keeps every file it has
# seen, addressed by its SHA-1. The SHA-1 comes from snapshot's API at fetch time, so
# nothing extra has to be pinned or bumped; the SHA256 in env.sh still decides whether the
# bytes are accepted. fetch_file asks only after every other source has
# failed, so while the pool still has the file snapshot is never contacted.
#
# Native packages only (<src>_<version>.tar.*): an .orig tarball's name carries the
# upstream version, not the Debian one the API is keyed by.
debian_snapshot_url() {
    local re='^https?://deb\.debian\.org/debian/pool/[^/]+/[^/]+/([^/]+)/([^/_]+)_([^/]+)\.tar\.[a-z0-9]+$'
    [[ $1 =~ $re ]] || return 0
    local src=${BASH_REMATCH[1]} file=${1##*/} version=${BASH_REMATCH[3]} sha1
    [ "${BASH_REMATCH[2]}" = "$src" ] || return 0
    command -v jq >/dev/null || { echo "  (no jq: snapshot.debian.org fallback skipped)" >&2; return 0; }
    sha1=$(fetch "https://snapshot.debian.org/mr/package/$src/${version//+/%2B}/srcfiles?fileinfo=1" 2>/dev/null \
        | jq -r --arg f "$file" '.fileinfo | to_entries[] | select(any(.value[]; .name == $f)) | .key' 2>/dev/null \
        | head -n1) || true
    [ -n "$sha1" ] && echo "https://snapshot.debian.org/file/$sha1/$file"
    return 0
}

verify() {
    [ "$(sha256sum <"$1" | cut -d' ' -f1)" = "$2" ]
}

packages=("$@")
if [ ${#packages[@]} -eq 0 ]; then
    mapfile -t packages < <(all_packages)
fi

failed=()
for pkg in "${packages[@]}"; do
    pkg=$(package_name "$pkg")
    # One unreachable upstream should not hide the state of the other 22, so collect the
    # failures and report them together at the end.
    ( fetch_one "$pkg" ) || failed+=("$pkg")
done

if [ ${#failed[@]} -gt 0 ]; then
    echo "error: failed to fetch: ${failed[*]}" >&2
    exit 1
fi
