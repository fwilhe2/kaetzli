#!/bin/bash
# Build everything and assemble the images, locally, in one go:
#
#     ./tools/build-image.sh                 # glibc, kernel, every package, then erofs + oci
#     ./tools/build-image.sh --clean         # delete staging/ and rootfs/ first
#     ./tools/build-image.sh --image-only    # skip the packages, just re-assemble the images
#     ./tools/build-image.sh --flavour erofs # only one of erofs / oci
#     ./tools/build-image.sh --skip kernel,systemd   # leave packages out of this run
#     ./tools/build-image.sh --only strace   # build just these packages, then the images
#     ./tools/build-image.sh --allow-unresolved   # assemble despite missing libraries (see below)
#
# Before assembling, test/check-rootfs-deps.py must pass on rootfs/, as it must in CI's
# rootfs job: a binary needing a library the image does not ship starts nowhere, and an
# image that boots far enough to show it is a slow way to find out. --allow-unresolved
# turns that failure back into a warning, for a look at a half-built tree; never in CI.
#
# Packages are built one after another. Each installs into its own staging/<pkg>/, so
# they no longer race on a shared tree, but every build ends by re-merging rootfs/ from
# all of them (tools/merge-staging.sh), and two merges at once would. glibc goes first
# because everything else compiles against it; the rest follow in alphabetical order,
# which is fine since CI builds them independently against a glibc-only sysroot too.
#
# ADDITIONAL FILES, opt-in per build: --extra DIR copies DIR over both assembled images,
# laid out like the target filesystem. extra/ is gitignored for the purpose, but nothing
# reads it unless named, and CI never does:
#
#     ./tools/build-image.sh --image-only --extra extra
#
#     extra/usr/bin/my-static-strace
#     extra/etc/motd
#     extra/etc/profile.d/local.sh
#
# Every path added is printed, written to output/extra-files-<flavour>.txt with its hash,
# and named in the SBOM (SPDXRef-LocalExtra). An overlay can only add: it is refused if it
# would replace anything the build installed, write outside usr/, etc/ or opt/, touch
# authentication, privilege, boot-time or provenance configuration (PAM, ssh, systemd,
# tmpfiles, sysctl, accounts, the loader's, /etc/containers, usr/share/kaetzli and licenses;
# the list is in image/build-rootfs.sh), or carry setuid, setgid or file capabilities.
# Those changes belong in image/files or a package, where review sees them.
#
# Extra *software* built from source belongs in packages/ instead, like any other package:
# once it has an env.sh + build.sh, this script picks it up automatically.
#
# A long run: use `run_in_background` or a terminal multiplexer.
set -euo pipefail
cd "$(dirname "$0")/.."
source tools/lib.sh

clean=0 image_only=0 allow_unresolved=0 flavours="erofs oci" skip="" only="" extra=""
while [ $# -gt 0 ]; do
    case "$1" in
        --clean)      clean=1 ;;
        --image-only) image_only=1 ;;
        --allow-unresolved) allow_unresolved=1 ;;
        --flavour)    flavours="${2:?--flavour needs erofs or oci}"; shift ;;
        --skip)       skip="${2:?--skip needs a comma-separated list}"; shift ;;
        --only)       only="${2:?--only needs a comma-separated list}"; shift ;;
        --extra)      extra="${2:?--extra needs a directory}"; shift ;;
        -h|--help)    sed -n '2,/^set -e/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
    shift
done

for f in $flavours; do
    case "$f" in erofs|oci) ;; *) echo "error: unknown flavour '$f'" >&2; exit 2 ;; esac
done

extra_mount=()
if [ -n "$extra" ]; then
    [ -d "$extra" ] || { echo "error: --extra $extra is not a directory" >&2; exit 2; }
    extra_mount=(--volume "$(cd "$extra" && pwd)":/extra:ro)
elif [ -n "$(ls -A extra 2>/dev/null)" ]; then
    echo "note: extra/ is not empty and is NOT overlaid; pass --extra extra to use it" >&2
fi

if [ "$clean" = 1 ]; then
    echo "==> removing staging/, rootfs/ and extensions/"
    # Files were created by the container's root, which is a subuid from out here.
    podman unshare rm -rf staging rootfs extensions 2>/dev/null || rm -rf staging rootfs extensions
fi

if [ "$image_only" = 0 ]; then
    all=$(all_packages)
    if [ -n "$only" ]; then
        order=$(echo "$only" | tr ',' ' ')
    else
        # glibc must be first; everything else in listing order.
        order="glibc $(echo "$all" | grep -vx glibc | tr '\n' ' ')"
    fi
    skip_list=",$skip,"
    total=$(echo $order | wc -w) n=0
    for pkg in $order; do
        n=$((n + 1))
        if [[ "$skip_list" == *",$pkg,"* ]]; then
            echo "==> [$n/$total] $pkg (skipped)"
            continue
        fi
        echo "==> [$n/$total] $pkg  ($(date +%H:%M:%S))"
        ./build.sh "$pkg"
    done
fi

if [ ! -e rootfs/usr/lib/libc.so.6 ]; then
    echo "error: rootfs/ has no glibc; nothing to assemble" >&2
    exit 1
fi

echo "==> checking the staging tree"
if ! ./test/check-rootfs-deps.py rootfs; then
    if [ "$allow_unresolved" = 1 ]; then
        echo "warning: unresolved libraries (see above); assembling anyway (--allow-unresolved)" >&2
    else
        echo "error: unresolved libraries (see above): build the package that ships them, or" >&2
        echo "       configure the dependency out (CLAUDE.md, 'the three things that break" >&2
        echo "       silently'). --allow-unresolved assembles anyway, for a look only." >&2
        exit 1
    fi
fi

echo "==> assembling images"
mkdir -p output
# Rebuilt every time: image/files is COPYed into it, so a stale image ships a stale /etc.
podman build -q -t rootfs-builder -f image/Containerfile .

build=${KAETZLI_BUILD:-$(git rev-parse HEAD 2>/dev/null || echo local)}
for f in $flavours; do
    echo "==> image: $f"
    podman run --rm --security-opt label=disable \
        -e KAETZLI_BUILD="$build" \
        -e SOURCE_DATE_EPOCH="$KAETZLI_SOURCE_DATE_EPOCH" \
        --volume "$PWD/rootfs":/usr/local/src:ro \
        "${extra_mount[@]}" \
        --volume "$PWD/output":/usr/local/output \
        rootfs-builder /usr/local/bin/build-rootfs.sh "$f"
done

# And the system extensions, from what packages with EXTENSION= in their env.sh staged
# into extensions/<name>/: a sysext and a confext each, in output/extensions/ and
# output/confexts/ (docs/guides/sysext.md).
for ext in extensions/*/; do
    [ -d "$ext" ] || continue
    name=$(basename "$ext")
    echo "==> extension: $name"
    podman run --rm --security-opt label=disable \
        -e SOURCE_DATE_EPOCH="$KAETZLI_SOURCE_DATE_EPOCH" \
        --volume "$PWD/$ext":/usr/local/src:ro \
        --volume "$PWD/output":/usr/local/output \
        rootfs-builder /usr/local/bin/build-extension.sh staged "$name"
done

echo
echo "done:"
ls -lh output/rootfs.erofs output/kaetzli-oci.tar output/extensions/*.raw output/confexts/*.raw 2>/dev/null || true
echo "boot it:  ./tools/boot-qemu.sh   (or ./test/systemd.py output/rootfs.erofs rootfs/boot/bzImage)"
