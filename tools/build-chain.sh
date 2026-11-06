#!/bin/bash
# The packages of an extension that compile against each other (DEPENDS=, build.sh), in
# the order they have to be built:
#
#     ./tools/build-chain.sh list               # extensions with such packages, one per line
#     ./tools/build-chain.sh packages <ext>     # its chained packages, in build order
#     ./tools/build-chain.sh key <ext>          # a cache key over everything they are built from
#     ./tools/build-chain.sh build <ext>        # ./build.sh each, in order
#
# CI's build matrix builds every package on its own against a glibc-only sysroot, which a
# package with DEPENDS= cannot be. So an extension with dependency edges is built in one
# job instead (ci.yml's chain job), one package after another, the way
# docs/proposals/extension-dependencies.md starts: a package is chained if it has
# DEPENDS= or another package names it there. The base packages they depend on (systemd)
# still come from the matrix, as artifacts unpacked into staging/ first.
set -euo pipefail
cd "$(dirname "$0")/.."
source tools/lib.sh

# Every package named in someone's DEPENDS=, and every package with one.
edges() {
    local pkg dep
    for pkg in $(all_packages); do
        for dep in $(env_get "$pkg" DEPENDS); do
            printf '%s\n%s\n' "$pkg" "$dep"
        done
    done | sort -u
}

# chained <ext> — the chained packages of an extension, each after its dependencies.
chained() {
    local ext=$1 pkg
    local in_ext
    in_ext=$(edges | while read -r pkg; do
                 [ "$(env_get "$pkg" EXTENSION)" = "$ext" ] && echo "$pkg"
             done)
    for pkg in $in_ext; do
        package_depends "$pkg"
        echo "$pkg"
    done | awk '!seen[$0]++' | grep -xF "$in_ext"
}

case "${1:-}" in
    list)
        edges | while read -r pkg; do
            ext=$(env_get "$pkg" EXTENSION)
            if [ -z "$ext" ] && [ -n "$(env_get "$pkg" DEPENDS)" ]; then
                echo "error: $pkg is a base package with DEPENDS=, which CI cannot build yet" >&2
                exit 1
            fi
            [ -z "$ext" ] || echo "$ext"
        done | sort -u ;;
    packages)
        chained "${2:?usage: $0 packages <extension>}" ;;
    key)
        ext=${2:?usage: $0 key <extension>}
        # What tools/build-package's key hashes for one package, for each chained package
        # and everything in its dependency closure, base packages included: a systemd bump
        # changes the libudev wlroots links.
        closure=$(for pkg in $(chained "$ext"); do package_depends "$pkg"; echo "$pkg"; done | awk '!seen[$0]++')
        files=(build.sh tools/lib.sh tools/build-chain.sh builder/build-package.sh
               packages/glibc/env.sh packages/glibc/build.sh)
        for pkg in $closure; do
            files+=("packages/$pkg/env.sh")
            [ -f "packages/$pkg/build.sh" ] && files+=("packages/$pkg/build.sh")
        done
        {
            cat "${files[@]}"
            ./tools/image-tags.sh builder
            for pkg in $closure; do ./tools/patch-cves.sh "$pkg"; done
            for pkg in glibc $closure; do
                find "packages/$pkg/src" "packages/$pkg/licenses" "packages/$pkg/patches" -type f -print0 2>/dev/null
            done | sort -z | xargs -0r cat
        } | md5sum | cut -d' ' -f1 ;;
    build)
        for pkg in $(chained "${2:?usage: $0 build <extension>}"); do
            echo "==> $pkg  ($(date +%H:%M:%S))"
            ./build.sh "$pkg"
        done ;;
    *)
        sed -n '2,/^set -e/p' "$0" | sed '$d;s/^# \{0,1\}//'
        exit 2 ;;
esac
