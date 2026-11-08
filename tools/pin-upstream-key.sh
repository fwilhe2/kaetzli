#!/usr/bin/env bash
# Pin the key that signed a package's current release into packages/<pkg>/upstream-key.asc:
#
#     ./tools/pin-upstream-key.sh <pkg> [keyring-url]
#
# The key is found by the fingerprint in the signature of the release env.sh pins now
# (SIG_URL), from keyserver.ubuntu.com, keys.openpgp.org, GNU's keyring for a GNU project,
# or keyring-url (a project's own KEYS file), and kept only if it verifies the pinned
# tarball — bytes the SHA256 already holds us to. So what is pinned is "the key that
# signed what we ship", whichever server handed it over; the server is not trusted for
# anything else. It is added beside any key already there, minimal (no third-party
# signatures), and tools/verify-signature.sh then holds every later release to the set.
#
# Run it for a package's first key, and when upstream signs with a new one: check the new
# key is upstream's (their release announcement, website, a signature by the old key)
# before committing, since from then on it is trusted for every release.
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

pkg=$(package_name "${1:?usage: $0 <pkg> [keyring-url]}")
extra=${2:-}
mapfile -t v < <(env_get "$pkg" SIG_URL TARBALL URL)
sig_url=${v[0]} tarball=${v[1]} url=${v[2]}
[ -n "$sig_url" ] || { echo "error: $pkg sets no SIG_URL in packages/$pkg/env.sh" >&2; exit 1; }
key="packages/$pkg/upstream-key.asc"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -m 700 "$tmp/find" "$tmp/keep"

./tools/fetch-sources.sh "$pkg" >/dev/null
"${CURL_DOWNLOAD[@]}" -o "$tmp/sig" "$sig_url"

# The signer: the issuer fingerprint subpacket, or for an older signature its key ID.
packets=$(gpg --homedir "$tmp/find" --batch --list-packets "$tmp/sig" 2>/dev/null || true)
id=$(sed -n 's/.*issuer fpr v[0-9] \([0-9A-F]\{40,64\}\).*/\1/p' <<< "$packets" | head -n1)
[ -n "$id" ] || id=$(sed -n 's/.*keyid \([0-9A-F]\{16\}\).*/\1/p' <<< "$packets" | head -n1)
[ -n "$id" ] || { echo "error: $sig_url names no signing key" >&2; exit 1; }
echo "$pkg: $tarball is signed by $id"

sources=("https://keyserver.ubuntu.com/pks/lookup?op=get&options=mr&search=0x$id")
if [ ${#id} -ge 40 ]; then
    sources+=("https://keys.openpgp.org/vks/v1/by-fingerprint/$id")
else
    sources+=("https://keys.openpgp.org/vks/v1/by-keyid/$id")
fi
case "$url" in */gnu/*|*.gnu.org/*) sources+=("https://ftp.gnu.org/gnu/gnu-keyring.gpg") ;; esac
[ -z "$extra" ] || sources+=("$extra")

found=
for src in "${sources[@]}"; do
    rm -f "$tmp/keys"
    fetch -o "$tmp/keys" "$src" 2>/dev/null || continue
    gpg --homedir "$tmp/find" --batch --quiet --import "$tmp/keys" 2>/dev/null || true
    # Kept only if it verifies the bytes env.sh pins; VALIDSIG's last field is the
    # primary key's fingerprint, whichever subkey signed. GOODSIG too, not EXPKEYSIG:
    # keyservers hold stale copies whose expiry the owner has since extended, which gpg
    # still calls valid, but export-minimal drops their expired subkeys and gpgv then
    # finds no key at all. The imports accumulate, so a later source's newer
    # self-signature brings such a copy up to date.
    status=$(gpg --homedir "$tmp/find" --batch --status-fd 1 --verify "$tmp/sig" "downloads/$tarball" 2>/dev/null || true)
    found=$(sed -n 's/^\[GNUPG:\] VALIDSIG .* \([0-9A-F]\{40,64\}\)$/\1/p' <<< "$status" | head -n1)
    grep -q '^\[GNUPG:\] GOODSIG ' <<< "$status" || found=
    if [ -n "$found" ]; then
        echo "$pkg: $found from $src verifies the pinned tarball"
        break
    fi
done
[ -n "$found" ] || { echo "error: no source had a key that verifies $tarball (try a keyring-url)" >&2; exit 1; }

# The new key, minimal, beside what was already pinned; everything in the file is kept.
[ ! -f "$key" ] || gpg --homedir "$tmp/keep" --batch --quiet --import "$key"
gpg --homedir "$tmp/find" --batch --export --export-options export-minimal "$found" |
    gpg --homedir "$tmp/keep" --batch --quiet --import
gpg --homedir "$tmp/keep" --batch --armor --export --export-options export-minimal > "$tmp/out.asc"
gpg --homedir "$tmp/keep" --batch --list-keys --with-colons |
    awk -F: '$1 == "pub" { p = 1 } $1 == "fpr" && p { f = $10; p = 0 } $1 == "uid" && f { print "  " f "  " $10; f = "" }'
mv "$tmp/out.asc" "$key"
echo "$pkg: $key updated; check the key is upstream's before committing it"
