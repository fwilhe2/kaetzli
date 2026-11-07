#!/usr/bin/env bash
# Sign the unified kernel image and make the firmware that only boots what we signed:
#
#     ./tools/sign-uki.sh [UKI]                          # a throwaway key: tests, local use
#     ./tools/sign-uki.sh --key K.pem --cert C.pem [UKI] # a real one (the release's)
#
# UKI defaults to output/kaetzli.efi. Into output/secureboot/ (OUT=): kaetzli.efi, it
# signed; ovmf-code.fd, OVMF's Secure Boot build; ovmf-vars.fd, its variable store with the certificate as PK,
# KEK and db and Secure Boot on; ovmf-vars-off.fd, the same firmware's defaults with
# Secure Boot off (test/secureboot.py's control); db.crt, the certificate. Boot those three together
# (SECUREBOOT=output/secureboot, kaetzli.qemu) and the firmware refuses any other kernel,
# and the UKI's stub ignores any command line it is passed: the root hash it carries is
# the one that boots (docs/guides/secure-boot.md).
#
# Assembly leaves the UKI unsigned so that it is reproducible; this is the step after, as
# attestation is. Without --key it makes a throwaway key, says so, and never reuses it:
# what it signs proves the mechanism, not who built the image. Runs in the assembler
# image (tools/prep.sh assembler), whose pinned Debian has sbsign, OVMF and virt-fw-vars.
# amd64 only for now.
set -euo pipefail
cd "$(dirname "$0")/.."
source tools/lib.sh

key="" cert="" uki=output/kaetzli.efi
while [ $# -gt 0 ]; do
    case "$1" in
        --key) key=$2; shift 2 ;;
        --cert) cert=$2; shift 2 ;;
        -*) echo "usage: $0 [--key KEY.pem --cert CERT.pem] [UKI]" >&2; exit 2 ;;
        *) uki=$1; shift ;;
    esac
done
if [ -n "$key$cert" ] && { [ ! -f "$key" ] || [ ! -f "$cert" ]; }; then
    echo "error: --key and --cert name two files, a PEM private key and its certificate" >&2
    exit 2
fi
[ "$(normalize_arch)" = amd64 ] || { echo "error: Secure Boot is amd64 only for now" >&2; exit 1; }

[ -f "$uki" ] || { echo "error: no $uki (tools/build-image.sh assembles it)" >&2; exit 1; }
out=${OUT:-output/secureboot}
rm -rf "$out"
mkdir -p "$out"

keys=$(mktemp -d)
trap 'rm -rf "$keys"' EXIT
if [ -n "$key" ]; then
    cp "$key" "$keys/db.key"
    cp "$cert" "$keys/db.crt"
else
    echo ">> no --key: signing with a throwaway key, for tests and local use only"
fi

# The owner GUID of the enrolled certificates, ours: fixed, so the variable store depends
# only on the certificate.
owner=8d5b5c0e-6b8f-4c5a-9d2e-4b6174a7a1e1

assembler=$(./tools/prep.sh assembler)
podman run --rm --network=none --security-opt label=disable \
    -v "$(realpath "$uki"):/in/kaetzli.efi:ro" -v "$keys:/keys" -v "$(realpath "$out"):/out" \
    "$assembler" bash -euc '
        if [ ! -f /keys/db.key ]; then
            openssl req -quiet -new -x509 -newkey rsa:3072 -nodes -days 30 -sha256 \
                -subj "/CN=kaetzli throwaway Secure Boot key/" \
                -keyout /keys/db.key -out /keys/db.crt
        fi
        sbsign --key /keys/db.key --cert /keys/db.crt --output /out/kaetzli.efi /in/kaetzli.efi
        sbverify --cert /keys/db.crt /out/kaetzli.efi
        cp /usr/share/OVMF/OVMF_CODE_4M.secboot.fd /out/ovmf-code.fd
        virt-fw-vars --input /usr/share/OVMF/OVMF_VARS_4M.fd --output /out/ovmf-vars.fd \
            --set-pk '"$owner"' /keys/db.crt --add-kek '"$owner"' /keys/db.crt \
            --add-db '"$owner"' /keys/db.crt --secure-boot >/dev/null
        virt-fw-vars --input /usr/share/OVMF/OVMF_VARS_4M.fd --output /out/ovmf-vars-off.fd \
            >/dev/null 2>&1
        cp /keys/db.crt /out/db.crt
    '
echo ">> $out: kaetzli.efi (signed), ovmf-code.fd, ovmf-vars.fd, ovmf-vars-off.fd, db.crt"
