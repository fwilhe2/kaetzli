#!/usr/bin/env bash
# Print the fully qualified reference of one of the two build-input images:
#
#     ./tools/image-tags.sh builder    # the toolchain everything compiles in
#     ./tools/image-tags.sh sources    # every pinned tarball, FROM scratch
#     ./tools/image-tags.sh assembler  # the pinned tools images are assembled with
#
# Both tags are content hashes, so the same checkout always names the same image and CI
# and a local tree agree without having to coordinate. Override the registry with
# REGISTRY= (localhost, for instance, to work entirely offline).
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

REGISTRY="${REGISTRY:-ghcr.io/fwilhe2/kaetzli}"

# The builder is compiled software and every build here is native, so there is one per
# architecture and the tag has to say which — two runners pushing the same tag would
# clobber each other. The sources image is data, so it is one manifest list for both.
ARCH=$(normalize_arch "${ARCH:-}")

case "${1:-}" in
    builder)
        # Everything that changes what is installed in the image, and nothing else: the
        # dependency list, how it is installed, the upstream toolchains' pins and keys,
        # and the entrypoint baked in beside it.
        hash=$(cat builder/Containerfile builder/deps.txt builder/build-package.sh \
                   builder/toolchains/* | sha256sum)
        echo "$REGISTRY/builder:${hash:0:16}-$ARCH"
        exit 0
        ;;
    assembler)
        # image/Containerfile alone: the pin and the apt line. The scripts and image/files
        # are layered on locally (image/Containerfile.scripts) and are not in this image.
        hash=$(sha256sum < image/Containerfile)
        echo "$REGISTRY/assembler:${hash:0:16}-$ARCH"
        exit 0
        ;;
    sources)
        # Every pinned version and its checksum, and the patches. A version bump changes
        # the tag, which is the point — the image contents changed. Nothing else in env.sh
        # matters here:
        # a changed URL that still yields the same bytes is the same image.
        # LOCAL_SOURCE packages contribute nothing: they put no tarball in the image, so
        # bumping one must not invalidate a tag that describes the tarballs.
        hash=$(for pkg in $(all_packages); do
                   ( load_env "$pkg"
                     if [ -z "${LOCAL_SOURCE:-}" ]; then
                         printf '%s %s %s\n' "$PKG" "$TARBALL" "$SHA256"
                         if [ -n "${VENDOR_TARBALL:-}" ]; then
                             printf '%s %s %s\n' "$PKG" "$VENDOR_TARBALL" "$VENDOR_SHA256"
                         fi
                         # Its Go modules: the tarball's go.sum pins them, the flag says
                         # they are in the image.
                         [ -z "${GO_MODULES:-}" ] || printf '%s go-modules\n' "$PKG"
                         # The patches it carries (tools/prep.sh copies them in). Nothing
                         # for a package without any, so this leaves every other tag alone.
                         digest=$(patches_digest "$PKG")
                         [ -z "$digest" ] || printf '%s patches %s\n' "$PKG" "$digest"
                     fi )
               # LC_ALL=C: under en_US.UTF-8 sort orders these lines differently, and a
               # local tree named a tag CI never pushed, so prep.sh rebuilt the image.
               done | LC_ALL=C sort | sha256sum)
        ;;
    *)
        echo "usage: $0 builder|sources|assembler" >&2
        exit 1
        ;;
esac

echo "$REGISTRY/${1}:${hash:0:16}"
