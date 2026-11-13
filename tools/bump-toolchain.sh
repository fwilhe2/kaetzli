#!/usr/bin/env bash
# Move one of the builder's upstream toolchains to a new release:
#
#     ./tools/bump-toolchain.sh go 1.27.5
#     ./tools/bump-toolchain.sh rust 1.101.0
#
# tools/bump-version.sh for builder/toolchains/<name>.sh: VERSION and every
# SHA256_<component>_<arch> are written together, each from a tarball that first verified
# against builder/toolchains/<name>-key.asc. A release signed by any other key stops here:
# pinning a new key is a person's commit, never an update pull request's. On any failure,
# or an interrupt, the pin file is put back as it was.
#
# Every toolchain bump changes the builder tag, so CI rebuilds the builder and every
# package after it.
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

[ $# -eq 2 ] || { echo "usage: $0 <toolchain> <version>" >&2; exit 2; }
name=$1 new=$2
pin=builder/toolchains/$name.sh
[ -f "$pin" ] || { echo "error: no toolchain '$name' (no $pin)" >&2; exit 1; }
[[ $new =~ ^[0-9][0-9A-Za-z.+~-]*$ ]] || { echo "error: not a version: $new" >&2; exit 1; }

# pin_var <VAR>, pin_file <component> <arch>: read with the pin file sourced, as it now is.
pin_var()  { bash -c 'source "$1"; printf %s "${!2}"' _ "$pin" "$1"; }
pin_file() { bash -c 'source "$1"; toolchain_file "$2" "$3"' _ "$pin" "$1" "$2"; }

current=$(pin_var VERSION)
tmp=$(mktemp -d)
cp "$pin" "$tmp/pin.orig"
cleanup() {
    local rc=$?
    [ "$rc" = 0 ] || cp "$tmp/pin.orig" "$pin"
    rm -rf "$tmp"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

sed -i -E "s|^VERSION=\"[^\"]*\"|VERSION=\"$new\"|" "$pin"
[ "$(pin_var VERSION)" = "$new" ] || { echo "error: $pin: VERSION not written" >&2; exit 1; }
gpg --batch --quiet --dearmor < "builder/toolchains/$name-key.asc" > "$tmp/keyring.gpg"
url_base=$(pin_var URL_BASE)
components=$(pin_var COMPONENTS)

for arch in amd64 arm64; do
    for comp in $components; do
        file=$(pin_file "$comp" "$arch")
        url="$url_base/$file"
        echo ">> $name: fetching $url" >&2
        if ! "${CURL_DOWNLOAD[@]}" -o "$tmp/$file" "$url" \
           || ! "${CURL_DOWNLOAD[@]}" -o "$tmp/$file.asc" "$url.asc"; then
            echo "error: $name $new: could not fetch $url (and its .asc)" >&2
            exit 1
        fi
        if ! out=$(gpgv --keyring "$tmp/keyring.gpg" "$tmp/$file.asc" "$tmp/$file" 2>&1); then
            echo "error: $file does not verify against builder/toolchains/$name-key.asc:" >&2
            sed 's/^/    /' <<< "$out" >&2
            echo "    A new signing key is pinned by hand, after checking it is upstream's." >&2
            exit 1
        fi
        sha=$(sha256sum "$tmp/$file" | cut -d' ' -f1)
        rm -f "$tmp/$file"
        var="SHA256_${comp//-/_}_$arch"
        sed -i -E "s|^$var=\"[0-9a-f]*\"|$var=\"$sha\"|" "$pin"
        # Read back: sed says nothing when its pattern does not match.
        [ "$(pin_var "$var")" = "$sha" ] || { echo "error: $pin: no $var line to write" >&2; exit 1; }
    done
done

echo "$name: $current -> $new"
