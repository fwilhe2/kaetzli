# shellcheck shell=bash
# The few things build.sh, tools/ and test/ all need, in one place rather than one copy
# per script — these had drifted into seven copies of the architecture switch, eight of
# "list every package" and three different ways of keeping one env.sh from leaking into
# the next.
#
#     source tools/lib.sh      # after cd-ing to the repository root
#
# Sourcing it defines functions, one array and one constant, and does nothing else. Paths
# are relative to the repository root, like everything else here.

# The one timestamp every build here uses in place of "now" (2026-01-01T00:00:00Z), handed
# to every package build by build.sh and to image assembly by tools/build-image.sh as
# SOURCE_DATE_EPOCH — the reproducible-builds.org convention that compilers, the kernel,
# systemd's tools, mkfs.erofs and image/build-rootfs.sh all honour. A constant rather than
# the commit's date on purpose: the date would make two builds of the same tree from
# different commits differ in every timestamp, and nothing here means "when" by it. It
# is not a version and does not need to move.
KAETZLI_SOURCE_DATE_EPOCH=1767225600

# normalize_arch [arch] — amd64 or arm64, from either spelling; the host's when empty.
normalize_arch() {
    case "${1:-$(uname -m)}" in
        x86_64|amd64)  echo amd64 ;;
        aarch64|arm64) echo arm64 ;;
        *) echo "error: unsupported architecture: $1 (expected amd64 or arm64)" >&2
           return 1 ;;
    esac
}

# all_packages — every package name, one per line, in directory order.
all_packages() {
    local e
    for e in packages/*/env.sh; do
        e="${e%/env.sh}"
        printf '%s\n' "${e#packages/}"
    done
}

# package_name <arg> — the package an argument names, accepting the path a shell
# tab-completes to (packages/coreutils/) as well as the bare name. Fails for an unknown one.
package_name() {
    local p="${1%/}"
    p="${p#packages/}"
    if [ -z "$p" ] || [ ! -f "packages/$p/env.sh" ]; then
        echo "error: unknown package '$p' (no packages/$p/env.sh)" >&2
        return 1
    fi
    printf '%s\n' "$p"
}

# load_env <pkg> — source the package's env.sh into the current shell, with $PKG set,
# since env.sh may use it in its URL. Nothing is unset first: a script that loads more
# than one package does it in a subshell, or uses env_get.
load_env() {
    PKG="$1"
    # shellcheck disable=SC1090
    source "packages/$1/env.sh"
}

# env_get <pkg> <VAR>... — print each variable from the package's env.sh on its own
# line (empty when unset), without touching the caller's shell.
env_get() (
    load_env "$1"
    shift
    for var; do printf '%s\n' "${!var:-}"; done
)

# Downloading a tarball: follow redirects, fail on HTTP errors, and retry the 5xx a mirror
# serves while it syncs. No --retry-delay, so curl backs off exponentially (1, 2, 4 … 32 s,
# about a minute in all): GitHub's release downloads answer 500 for longer than a fixed
# 2 s × 5 waited out. Not --retry-max-time, which counts from the first attempt and would
# leave a large tarball that fails late with no retry at all.
CURL_DOWNLOAD=(curl --location --fail --silent --show-error
               --retry 6 --retry-all-errors)

# fetch <curl args>... — an API or index request: short, retried (5xx, 429, timeouts;
# not a 404, which is an answer), backing off 1, 2, 4, 8 s, bounded.
fetch() {
    curl -fsSL --max-time 60 --retry 4 "$@"
}

# gh_api <url> — fetch with a GitHub token when there is one (GH_TOKEN, GITHUB_TOKEN or
# `gh auth token`), which lifts the anonymous rate limit.
gh_api() {
    local token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
    if [ -z "$token" ] && command -v gh >/dev/null; then
        token=$(gh auth token 2>/dev/null || true)
    fi
    if [ -n "$token" ]; then
        fetch -H "Authorization: Bearer $token" -H 'Accept: application/vnd.github+json' "$1"
    else
        fetch -H 'Accept: application/vnd.github+json' "$1"
    fi
}

# mib <bytes> — "12.3", rounded to the nearest tenth, in integer arithmetic.
mib() {
    local tenths=$(( ($1 * 10 + 524288) / 1048576 ))
    printf '%d.%d' $(( tenths / 10 )) $(( tenths % 10 ))
}

# extract_vmlinux <bzImage> <out> — the uncompressed kernel inside an x86 bzImage, which is
# what Firecracker loads there (through its PVH entry point; it does not take a bzImage).
# The payload is the first gzip stream after the setup code — packages/kernel/build.sh
# leaves the compression at defconfig's gzip — and gzip stops at the end of it, ignoring
# the trailer the bzImage carries after. arm64's Image is already uncompressed and needs
# none of this. Used by tools/boot-firecracker.sh
# (test/vmm.py has its own, kaetzli.boot.vmm.extract_vmlinux).
extract_vmlinux() {
    local off
    off=$(grep -abo $'\x1f\x8b\x08' "$1" | head -1 | cut -d: -f1)
    [ -n "$off" ] || { echo "error: no gzip payload in $1" >&2; return 1; }
    tail -c "+$((off + 1))" "$1" | gzip -dc 2>/dev/null > "$2" || true
    [ "$(head -c 4 "$2")" = $'\x7fELF' ] || { echo "error: $1 did not unpack to an ELF" >&2; return 1; }
}

# Patches: packages/<pkg>/patches/ (docs/guides/patches.md).
#
#   patches/upstream/*   a series upstream publishes as patches rather than as a release
#                        (bash's bash53-001…), owned by tools/sync-patches.sh, applied
#                        first, with -p$UPSTREAM_PATCH_STRIP (default 1)
#   patches/*.patch      ours, `git format-patch` style, applied after them with -p1
#
# Each list in C-locale order of the file name, so 0001-… before 0002-….

# package_patches <pkg> — the patch files, one per line, in the order they are applied.
package_patches() {
    local dir="packages/$1/patches" sub f
    for sub in upstream/ ''; do
        for f in "$dir/$sub"*; do
            [ -f "$f" ] || continue
            [ -n "$sub" ] || [[ $f == *.patch ]] || continue
            printf '%s\n' "$f"
        done | LC_ALL=C sort
    done
}

# patches_digest <pkg> — a hash of exactly which patches would be applied, for the
# extraction stamp, the CI cache key and the sources image tag. Empty when there are none.
patches_digest() {
    local list f
    list=$(package_patches "$1")
    [ -n "$list" ] || return 0
    while read -r f; do
        printf '%s %s\n' "${f#packages/$1/patches/}" "$(sha256sum <"$f" | cut -d' ' -f1)"
    done <<< "$list" | sha256sum | cut -d' ' -f1
}

# apply_patches <pkg> <source-dir> <builder-image> — apply every patch in order, in the
# builder (the host may have no `patch`, and this way every machine runs the same one),
# with --network=none like the compile. --fuzz=0: a patch that only applies by guessing is
# a patch that needs rebasing, and the guess could differ between `patch` versions.
apply_patches() {
    local pkg=$1 dir=$2 builder=$3 strip
    [ -n "$(package_patches "$pkg")" ] || return 0
    strip=$(env_get "$pkg" UPSTREAM_PATCH_STRIP)
    # Paths inside the container: packages/<pkg>/patches is mounted at /patches.
    package_patches "$pkg" | sed "s|^packages/$pkg/patches/|/patches/|" |
        podman run --rm -i --network=none --security-opt label=disable \
            --volume "$(realpath "$dir")":/src --volume "$PWD/packages/$pkg/patches":/patches:ro \
            --workdir /src --env STRIP="${strip:-1}" "$builder" bash -c '
                set -eu
                while read -r p; do
                    case $p in /patches/upstream/*) level=$STRIP ;; *) level=1 ;; esac
                    echo "  patching: ${p#/patches/} (-p$level)"
                    # A preamble meant to be fed to sh (ncurses: "remove these files, then
                    # patch") ends in `exit`. Its `rm -f <path>` lines are done here, and
                    # nothing else in it is run: the patch is data, never a script.
                    pre=$(awk "/^(Index: |--- |\*\*\* |diff )/ { exit } { print }" "$p")
                    if printf "%s\n" "$pre" | grep -qx exit; then
                        printf "%s\n" "$pre" | sed -n "/^[^#[:space:]]/p" | while read -r cmd; do
                            case $cmd in
                                exit) ;;
                                "rm -f "*)
                                    f=${cmd#rm -f }
                                    case $f in /*|*..*|*[!A-Za-z0-9._/+-]*)
                                        echo "error: ${p#/patches/}: refusing to remove $f" >&2; exit 1 ;; esac
                                    rm -f -- "$f" ;;
                                *) echo "error: ${p#/patches/}: preamble command not understood: $cmd" >&2; exit 1 ;;
                            esac
                        done
                    fi
                    if ! patch --batch --forward --fuzz=0 --no-backup-if-mismatch -p"$level" -i "$p" >/tmp/out 2>&1; then
                        cat /tmp/out >&2
                        echo "error: ${p#/patches/} does not apply" >&2
                        if grep -q "previously applied" /tmp/out; then
                            echo "       upstream has it already, it seems: delete the patch" >&2
                        fi
                        exit 1
                    fi
                done'
}
