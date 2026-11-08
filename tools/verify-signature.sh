#!/usr/bin/env bash
# Check each package's pinned tarball against the signature upstream published for it:
#
#     ./tools/verify-signature.sh              # every package that sets SIG_URL
#     ./tools/verify-signature.sh curl bash    # just these
#
# A SHA256 in env.sh holds every later download to the bytes the bump saw; it says
# nothing about whether those bytes were upstream's. tools/bump-version.sh pins whatever
# the URL served (trust on first use), and update PRs merge unattended, so a compromised
# release server or mirror would otherwise reach main within hours. Where upstream signs
# its releases, env.sh says where the signature is (SIG_URL, derived from URL) and
# packages/<pkg>/upstream-key.asc holds the keys allowed to have made it, pinned in git —
# the same file tools/sync-patches.sh verifies a patch series against. The signature must
# verify against those keys only, never against whatever a keyserver offers.
#
# bump-version.sh runs this before a bump can become a pull request; CI's sources job runs
# it on every pin. A release signed by a key that is not pinned fails here: that is a
# person's decision (a new maintainer, a rotated key), made with
# tools/pin-upstream-key.sh and reviewed as its own commit.
#
# Without SIG_URL a package is listed as unsigned and passes: not every upstream signs.
# MIRRORS are not tried for the signature; it comes from beside $URL, as the bump did.
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

for tool in gpg gpgv; do
    command -v "$tool" >/dev/null || { echo "error: $tool is needed to check signatures" >&2; exit 1; }
done

packages=("$@")
[ ${#packages[@]} -gt 0 ] || mapfile -t packages < <(all_packages)

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

failed=() unsigned=() good=0
for pkg in "${packages[@]}"; do
    pkg=$(package_name "$pkg")
    mapfile -t v < <(env_get "$pkg" SIG_URL TARBALL)
    sig_url=${v[0]} tarball=${v[1]}
    if [ -z "$sig_url" ]; then
        unsigned+=("$pkg")
        continue
    fi
    key="packages/$pkg/upstream-key.asc"
    if [ ! -f "$key" ]; then
        echo "error: $pkg sets SIG_URL but has no $key to verify it against" >&2
        failed+=("$pkg")
        continue
    fi
    # The pinned bytes, checksum verified, from downloads/ or fetched.
    if ! ./tools/fetch-sources.sh "$pkg" >/dev/null; then
        echo "error: $pkg: could not fetch its pinned tarball" >&2
        failed+=("$pkg")
        continue
    fi
    rm -f "$tmp/sig" "$tmp/keyring.gpg"
    if ! "${CURL_DOWNLOAD[@]}" -o "$tmp/sig" "$sig_url"; then
        echo "error: $pkg: no signature at $sig_url" >&2
        failed+=("$pkg")
        continue
    fi
    gpg --batch --quiet --dearmor < "$key" > "$tmp/keyring.gpg"
    if out=$(gpgv --keyring "$tmp/keyring.gpg" "$tmp/sig" "downloads/$tarball" 2>&1); then
        echo "  $pkg: $tarball: good signature from a key in $key"
        good=$((good + 1))
    else
        echo "error: $pkg: $tarball does not verify against $key:" >&2
        sed 's/^/    /' <<< "$out" >&2
        echo "    A new signing key is pinned by hand, after checking it is upstream's:" >&2
        echo "    ./tools/pin-upstream-key.sh $pkg" >&2
        failed+=("$pkg")
    fi
done

echo "$good signed and verified; ${#unsigned[@]} without SIG_URL${unsigned:+: ${unsigned[*]}}"
if [ ${#failed[@]} -gt 0 ]; then
    echo "error: signature check failed for: ${failed[*]}" >&2
    exit 1
fi
