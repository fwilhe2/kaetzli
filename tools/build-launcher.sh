#!/usr/bin/env bash
# Build the launcher (go/cmd/kaetzli, docs/proposals/launcher.md) for every platform it is
# released for, in the golang container of the toolchain go/go.mod names:
#
#     ./tools/build-launcher.sh [OUT] [VERSION]   # default output/launcher, "dev"
#
# Named as `uname -s` and `uname -m` print them, so the install line in the README works
# unchanged. Reproducible: no network, -trimpath, no build ID or VCS stamp, and the
# version the only thing a build is told.
set -euo pipefail

cd "$(dirname "$0")/.."

OUT="${1:-output/launcher}"
VERSION="${2:-dev}"
go_version=$(sed -n 's/^toolchain go//p' go/go.mod)
[ -n "$go_version" ] || { echo "error: go/go.mod names no toolchain" >&2; exit 1; }
command -v podman >/dev/null || { echo "error: podman not found (the launcher is compiled in a golang container)" >&2; exit 1; }

mkdir -p "$OUT"
# GOOS GOARCH and the name uname gives the pair.
targets="linux amd64 linux-x86_64
linux arm64 linux-aarch64"
while read -r goos goarch name; do
    echo ">> kaetzli-$name (Go $go_version)"
    podman run --rm --network=none \
        -v "$PWD/go:/src:ro,z" -v "$PWD/$OUT:/out:z" -w /src \
        -e GOOS="$goos" -e GOARCH="$goarch" -e CGO_ENABLED=0 -e GOTOOLCHAIN=local \
        -e GOCACHE=/tmp/gocache -e GOFLAGS=-buildvcs=false \
        "docker.io/library/golang:$go_version" \
        go build -trimpath -ldflags "-s -w -buildid= -X main.version=$VERSION" \
            -o "/out/kaetzli-$name" ./cmd/kaetzli
done <<< "$targets"
ls -la "$OUT"
