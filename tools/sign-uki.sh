#!/usr/bin/env bash
# Sign the unified kernel image and make the firmware that only boots what we signed:
#
#     ./tools/sign-uki.sh [UKI]                          # a throwaway key: tests, local use
#     ./tools/sign-uki.sh --key K.pem --cert C.pem [UKI] # your own (docs/guides/secure-boot.md)
#     ./tools/sign-uki.sh --ext podman,debug [--extensions DIR] [--var] [UKI]
#
# UKI defaults to output/kaetzli.efi. Into output/secureboot/ (OUT=): kaetzli.efi, it
# signed; ovmf-code.fd, OVMF's Secure Boot build; ovmf-vars.fd, its variable store with a
# PK and KEK made for it and thrown away, the certificate in db and Secure Boot on;
# ovmf-vars-off.fd, the same firmware's defaults with Secure Boot off (test/secureboot.py's
# control); db.crt, the certificate; sealed, what the UKI's command line was made for.
# Boot those together (SECUREBOOT=output/secureboot, kaetzli.qemu) and the firmware refuses
# any other kernel, and the UKI's stub ignores any command line it is passed: the root hash
# it carries is the one that boots.
#
# --ext writes those extensions' verity tables (from --extensions, default the UKI's
# directory) into the UKI's command line before signing, as `kaetzli start --secure-boot`
# does, so SYSEXTS boot under Secure Boot too; --var says the boot attaches a /var disk
# (VAR_DISK), which comes before the extensions' disks and so changes their names.
# kaetzli.qemu refuses a boot whose SYSEXTS or VAR_DISK differ from what `sealed` records.
#
# Assembly leaves the UKI unsigned so that it is reproducible; this is the step after, as
# attestation is. Without --key it makes a throwaway key, says so, and never writes it:
# what it signs proves the mechanism, not who built the image. The signing and the
# variable store are the launcher's code (go/cmd/kaetzli secureboot-files, the same as
# `kaetzli start`), run in the golang container go/go.mod names; the firmware comes from
# the pinned assembler image (tools/prep.sh assembler). amd64 only for now.
set -euo pipefail
cd "$(dirname "$0")/.."
source tools/lib.sh

key="" cert="" uki=output/kaetzli.efi exts="" extdir="" with_var=0
while [ $# -gt 0 ]; do
    case "$1" in
        --key) key=$2; shift 2 ;;
        --cert) cert=$2; shift 2 ;;
        --ext) exts=$2; shift 2 ;;
        --extensions) extdir=$2; shift 2 ;;
        --var) with_var=1; shift ;;
        -*) echo "usage: $0 [--key KEY.pem --cert CERT.pem] [--ext a,b [--extensions DIR] [--var]] [UKI]" >&2; exit 2 ;;
        *) uki=$1; shift ;;
    esac
done
if [ -n "$key$cert" ] && { [ ! -f "$key" ] || [ ! -f "$cert" ]; }; then
    echo "error: --key and --cert name two files, a PEM private key and its certificate" >&2
    exit 2
fi
[ "$(normalize_arch)" = amd64 ] || { echo "error: Secure Boot is amd64 only for now" >&2; exit 1; }
[ -f "$uki" ] || { echo "error: no $uki (tools/build-image.sh assembles it)" >&2; exit 1; }
extdir=${extdir:-$(dirname "$uki")}
go_version=$(sed -n 's/^toolchain go//p' go/go.mod)
[ -n "$go_version" ] || { echo "error: go/go.mod names no toolchain" >&2; exit 1; }

out=${OUT:-output/secureboot}
rm -rf "$out"
mkdir -p "$out"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# The firmware, from the pinned assembler image: the code as it is, the store's template
# for the Go side to enrol into.
assembler=$(./tools/prep.sh assembler)
podman run --rm --network=none --security-opt label=disable -v "$(realpath "$out"):/out" -v "$tmp:/tmp/fw" \
    "$assembler" sh -euc 'cp /usr/share/OVMF/OVMF_CODE_4M.secboot.fd /out/ovmf-code.fd
                          cp /usr/share/OVMF/OVMF_VARS_4M.fd /tmp/fw/template.fd'

args=(--uki /in/kaetzli.efi --template /fw/template.fd --out /out)
mounts=(-v "$(realpath "$uki"):/in/kaetzli.efi:ro" -v "$tmp:/fw:ro" -v "$(realpath "$out"):/out")
if [ -n "$key" ]; then
    mounts+=(-v "$(realpath "$key"):/keys/db.key:ro" -v "$(realpath "$cert"):/keys/db.crt:ro")
    args+=(--key /keys/db.key --cert /keys/db.crt)
fi
if [ -n "$exts" ]; then
    mounts+=(-v "$(realpath "$extdir"):/ext:ro")
    args+=(--extensions /ext)
    IFS=, read -ra list <<< "$exts"
    for e in "${list[@]}"; do args+=(--ext "$e"); done
fi
[ "$with_var" = 0 ] || args+=(--var)
podman run --rm --network=none --security-opt label=disable \
    -v "$PWD/go:/src:ro" -w /src -e GOTOOLCHAIN=local -e GOCACHE=/tmp/gocache -e GOFLAGS=-buildvcs=false \
    "${mounts[@]}" "docker.io/library/golang:$go_version" go run ./cmd/kaetzli secureboot-files "${args[@]}"
echo ">> $out: kaetzli.efi (signed), ovmf-code.fd, ovmf-vars.fd, ovmf-vars-off.fd, db.crt, sealed"
