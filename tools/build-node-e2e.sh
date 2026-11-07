#!/usr/bin/env bash
# Build Kubernetes' node e2e suite for test/node-conformance.py: e2e_node.test and the
# ginkgo runner that drives it in parallel, from the tarball packages/kubernetes pins, so
# the tests are the ones written for the kubelet the extension ships.
#
#     ./tools/build-node-e2e.sh            # into output/node-e2e/<arch>/
#
# Not a package: the suite is a 200 MB test binary with etcd and kube-apiserver linked in,
# and nothing a node should carry. It is static (CGO_ENABLED=0) and runs in the guest
# beside the extension's kubelet, which it starts itself.
#
# The compile is in the golang image of the version Kubernetes pins in .go-version, with
# --network=none — every module is vendored in the tarball — so it does not depend on the
# host's Go, and CI and a laptop build the same thing. Rebuilt only when the Kubernetes
# version changes.
#
# Overrides: ARCH (amd64 or arm64, default the host's), OUT (default output/node-e2e/$ARCH).
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

ARCH=$(normalize_arch "${ARCH:-}")
OUT="${OUT:-output/node-e2e/$ARCH}"
mapfile -t pin < <(env_get kubernetes VERSION TARBALL URL SHA256)
version="${pin[0]}" tarball="downloads/${pin[1]}" url="${pin[2]}" sha256="${pin[3]}"

if [ -x "$OUT/e2e_node.test" ] && [ -x "$OUT/ginkgo" ] && [ "$(cat "$OUT/VERSION" 2>/dev/null)" = "$version" ]; then
    echo ">> node e2e suite for Kubernetes v$version already in $OUT"
    exit 0
fi

command -v podman >/dev/null || { echo "error: podman not found (the suite is compiled in a golang container)" >&2; exit 1; }

mkdir -p downloads
if [ ! -f "$tarball" ]; then
    echo ">> downloading $url"
    "${CURL_DOWNLOAD[@]}" -o "$tarball.part" "$url"
    mv "$tarball.part" "$tarball"
fi
echo "$sha256  $tarball" | sha256sum --check --quiet || {
    echo "error: $tarball does not match packages/kubernetes/env.sh's SHA256" >&2; exit 1; }

src="output/node-e2e/src-$version"
if [ ! -f "$src/.go-version" ]; then
    echo ">> unpacking $tarball"
    rm -rf "$src.part"
    mkdir -p "$src.part"
    tar xzf "$tarball" -C "$src.part" --strip-components=1
    mv "$src.part" "$src"
fi
go_version=$(cat "$src/.go-version")

echo ">> building e2e_node.test and ginkgo for $ARCH, Kubernetes v$version, Go $go_version"
mkdir -p "$OUT"
rm -f "$OUT/VERSION"
podman run --rm --network=none \
    -v "$PWD/$src:/src:ro,z" -v "$PWD/$OUT:/out:z" -w /src \
    -e GOOS=linux -e GOARCH="$ARCH" -e CGO_ENABLED=0 -e GOFLAGS=-mod=vendor -e GOTOOLCHAIN=local \
    -e GOCACHE=/tmp/gocache \
    "docker.io/library/golang:$go_version" \
    sh -euc 'go test -c -vet=off -trimpath -o /out/e2e_node.test ./test/e2e_node
             go build -trimpath -o /out/ginkgo ./vendor/github.com/onsi/ginkgo/v2/ginkgo'
echo "$version" > "$OUT/VERSION"
ls -l "$OUT"
