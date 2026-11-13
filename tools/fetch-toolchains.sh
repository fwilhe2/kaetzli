#!/usr/bin/env bash
# The builder's upstream toolchains (builder/toolchains/), fetched and checked:
#
#     ./tools/fetch-toolchains.sh DIR [arch]
#
# Every pinned tarball for the architecture (the host's by default) goes into DIR, beside
# a SHA256SUMS of them, for builder/Containerfile to install (tools/prep.sh passes DIR as
# its `toolchain-tarballs` build context). Each file must match the SHA-256 its pin file
# names and verify against the key pinned beside it (builder/toolchains/<name>-key.asc):
# the checksum holds the bytes to the bump's, the signature holds the bump to upstream's.
# Tarballs and signatures are kept in downloads/toolchains/, so a rebuilt builder fetches
# nothing twice.
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

[ $# -ge 1 ] || { echo "usage: $0 DIR [arch]" >&2; exit 2; }
out=$1
arch=$(normalize_arch "${2:-}")

for tool in gpg gpgv sha256sum; do
    command -v "$tool" >/dev/null || { echo "error: $tool is needed to check the toolchains" >&2; exit 1; }
done

cache=downloads/toolchains
mkdir -p "$cache" "$out"
: > "$out/SHA256SUMS"
keyring=$(mktemp)
trap 'rm -f "$keyring"' EXIT

for name in $(all_toolchains); do
    gpg --batch --quiet --dearmor < "builder/toolchains/$name-key.asc" > "$keyring"
    while read -r file url sha; do
        [ -n "$sha" ] || { echo "error: builder/toolchains/$name.sh pins no SHA-256 for $file" >&2; exit 1; }
        if [ ! -f "$cache/$file" ] || ! echo "$sha  $cache/$file" | sha256sum --check --status; then
            echo ">> toolchains: fetching $url" >&2
            "${CURL_DOWNLOAD[@]}" -o "$cache/$file.part" "$url"
            mv "$cache/$file.part" "$cache/$file"
        fi
        if ! echo "$sha  $cache/$file" | sha256sum --check --status; then
            echo "error: $file: CHECKSUM MISMATCH against builder/toolchains/$name.sh" >&2
            exit 1
        fi
        [ -f "$cache/$file.asc" ] || "${CURL_DOWNLOAD[@]}" -o "$cache/$file.asc" "$url.asc"
        if ! verified=$(gpgv --keyring "$keyring" "$cache/$file.asc" "$cache/$file" 2>&1); then
            echo "error: $file does not verify against builder/toolchains/$name-key.asc:" >&2
            sed 's/^/    /' <<< "$verified" >&2
            exit 1
        fi
        ln -f "$cache/$file" "$out/$file" 2>/dev/null || cp "$cache/$file" "$out/$file"
        echo "$sha  $file" >> "$out/SHA256SUMS"
        echo ">> toolchains: $file: checksum and signature good" >&2
    done < <(toolchain_files "$name" "$arch")
done
