#!/usr/bin/env bash
# Whether a package's patches apply, without building anything:
#
#     ./tools/check-patches.sh                    # every package that has patches
#     ./tools/check-patches.sh gzip bash          # just these
#     ./tools/check-patches.sh --version 1.16 gzip   # against another upstream release
#     KEEP=1 ./tools/check-patches.sh gzip        # and leave the patched tree to look at
#
# It does what build.sh does at extraction — unpack the pinned tarball, apply
# packages/<pkg>/patches in order, in the builder image — into output/patch-check/<pkg>/,
# and stops there. Seconds, where a build is minutes; and nothing in packages/ or
# staging/ is touched. Needs the tarball (fetched once, verified) and the builder image
# (pulled once): `./tools/prep.sh builder` is the network step, as for a build.
#
# --version is the question a bump asks — do our patches still apply to the new release,
# or has upstream merged them? — answered before anything is bumped. That tarball is not
# pinned yet, so it is downloaded unverified into output/patch-check/, never downloads/.
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

version=
if [ "${1:-}" = --version ]; then
    version=${2:?--version needs a version}
    shift 2
    # Written into env.sh for eval below, so version characters only (as
    # python/src/kaetzli/updates/check.py's VERSION_CHARS): no quote, no sed delimiter.
    if ! [[ $version =~ ^[0-9A-Za-z][0-9A-Za-z._+~-]*$ ]]; then
        echo "error: '$version' is not a version" >&2
        exit 1
    fi
fi

packages=()
for arg; do packages+=("$(package_name "$arg")"); done
if [ ${#packages[@]} -eq 0 ]; then
    for pkg in $(all_packages); do
        [ -z "$(package_patches "$pkg")" ] || packages+=("$pkg")
    done
fi
if [ ${#packages[@]} -eq 0 ]; then
    echo "no package has patches"
    exit 0
fi

builder=$(./tools/prep.sh builder | tail -n1)
mkdir -p output/patch-check

failed=()
for pkg in "${packages[@]}"; do
    if [ -z "$(package_patches "$pkg")" ]; then
        echo "$pkg: no patches"
        continue
    fi
    work="output/patch-check/$pkg"
    rm -rf "$work"
    mkdir -p "$work/src"

    if [ -n "$version" ]; then
        # The URL env.sh would download for that version, as tools/check-updates.py finds it.
        url=$(PKG=$pkg; eval "$(sed -E "s/^VERSION=.*/VERSION=\"$version\"/" "packages/$pkg/env.sh")"; echo "$URL")
        tarball="$work/${url##*/}"
        echo "$pkg $version: fetching $url (unverified: not pinned yet)"
        "${CURL_DOWNLOAD[@]}" -o "$tarball" "$url"
    else
        ./tools/fetch-sources.sh "$pkg" >/dev/null
        tarball="downloads/$(env_get "$pkg" TARBALL)"
    fi
    tar -xf "$tarball" -C "$work/src" --strip-components=1
    # The vendored tree, as build.sh adds it: a patch may touch it.
    vendor=$(env_get "$pkg" VENDOR_TARBALL)
    if [ -n "$vendor" ] && [ -z "$version" ]; then
        tar -xf "downloads/$vendor" -C "$work/src"
    fi

    echo "$pkg${version:+ $version}:"
    if apply_patches "$pkg" "$work/src" "$builder"; then
        echo "$pkg: all $(package_patches "$pkg" | wc -l) patches apply"
    else
        failed+=("$pkg")
    fi
    if [ -n "${KEEP:-}" ]; then
        echo "$pkg: patched tree in $work/src"
    else
        rm -rf "$work"
    fi
done

if [ ${#failed[@]} -gt 0 ]; then
    echo "error: patches do not apply: ${failed[*]}" >&2
    exit 1
fi
