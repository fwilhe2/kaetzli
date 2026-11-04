#!/usr/bin/env bash
# Push the OCI images this build produced to a registry, as one multi-arch image:
#
#     ./tools/publish-oci.sh                      # both arches, from artifacts/
#     TAG=v1 ./tools/publish-oci.sh               # under a name of your choosing
#     REGISTRY=localhost:5000 ./tools/publish-oci.sh
#     DIGESTS=output/oci-digests.txt ./tools/publish-oci.sh
#
# DIGESTS names a file to write what was pushed to, one `<sha256 hex>  <image>:<tag>` per
# image and one for the list, in sha256sum's format: CI attests each digest (ci.yml,
# publish-oci), and a digest is what a signature can be about where a tag is not.
#
# It expects the two archives where CI's download-artifact leaves them,
# artifacts/oci-image-<arch>/kaetzli-oci.tar, which is also where tools/fetch-image.sh
# puts a downloaded one. Both have to be present: the point of this script is the
# manifest list, and a list with one entry is just an image with extra steps.
#
# Unlike the builder and sources images, this one is not content-addressed — it is the
# output of a build rather than an input to it, and what went into it is the whole
# repository. So the tag is the commit, and `latest` follows main.
#
# Nothing here needs skopeo or buildah. `podman load` reads the hand-written layout
# image/build-rootfs.sh produces, and podman is already the only container tool this
# repository uses.
set -euo pipefail

cd "$(dirname "$0")/.."

REGISTRY="${REGISTRY:-ghcr.io/fwilhe2/kaetzli}"
REPO="$REGISTRY/kaetzli"

# GITHUB_SHA in Actions, the working tree's HEAD locally. Twelve characters because
# that is long enough to be unambiguous and short enough to type.
sha="${GITHUB_SHA:-$(git rev-parse HEAD)}"
TAG="${TAG:-${sha:0:12}}"

# Also tag the list `latest`, unless something else was asked for: a moving name is
# what makes `podman run ghcr.io/.../kaetzli` work without looking a commit up first.
LATEST="${LATEST:-latest}"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
if [ -n "${DIGESTS:-}" ]; then
    mkdir -p "$(dirname "$DIGESTS")"
    : > "$DIGESTS"
fi
# record <ref>: the digest the last push wrote, against the name it was pushed as.
record() {
    local digest
    digest=$(< "$tmp/digest")
    case "$digest" in
        sha256:*) ;;
        *) echo "error: podman wrote no sha256 digest for $1: '$digest'" >&2; exit 1 ;;
    esac
    if [ -n "${DIGESTS:-}" ]; then
        printf '%s  %s\n' "${digest#sha256:}" "$1" >> "$DIGESTS"
    fi
}

per_arch=()
for arch in amd64 arm64; do
    archive="artifacts/oci-image-$arch/kaetzli-oci.tar"
    [ -f "$archive" ] || {
        echo "error: no OCI archive for $arch at $archive" >&2
        echo "       CI's rootfs job uploads one per architecture as oci-image-<arch>;" >&2
        echo "       download both before running this (tools/fetch-image.sh, or" >&2
        echo "       actions/download-artifact with pattern: oci-image-*)." >&2
        exit 1
    }

    # Both archives carry the same ref.name annotation, so both load as
    # localhost/kaetzli:latest and the second load moves that tag off the first image.
    # Hence tag and push inside the loop, before the next load: by the end of it
    # localhost/kaetzli:latest means arm64 and nothing should be relying on it.
    loaded=$(podman load --quiet --input "$archive" | tail -n1)
    ref=${loaded##*: }
    [ -n "$ref" ] || { echo "error: could not tell what podman loaded: $loaded" >&2; exit 1; }

    dest="$REPO:$TAG-$arch"
    podman tag "$ref" "$dest"
    podman push --digestfile "$tmp/digest" "$dest"
    record "$dest"
    echo ">> pushed $dest"
    per_arch+=("$dest")
done

# The two per-arch tags stay. They are what the list points at — a manifest list is
# digests, not tags, but an untagged manifest is a garbage collection candidate at most
# registries — and they are how you ask for the other architecture on purpose.
#
# `podman manifest create` adds to an existing list rather than replacing it, so a stale
# one from an interrupted run would survive with only its old entries refreshed. Remove
# it first; the same trap is handled the same way in tools/prep.sh.
podman manifest rm "$REPO:$TAG" >/dev/null 2>&1 || true
podman manifest create "$REPO:$TAG" "${per_arch[@]}"

podman manifest push --all --digestfile "$tmp/digest" "$REPO:$TAG"
record "$REPO:$TAG"
echo ">> pushed $REPO:$TAG (manifest list)"

if [ -n "$LATEST" ]; then
    podman manifest push --all "$REPO:$TAG" "docker://$REPO:$LATEST"
    echo ">> pushed $REPO:$LATEST (manifest list)"
fi
