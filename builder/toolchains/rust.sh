# Rust, from upstream's stable release rather than sid's rustc and cargo, for netavark and
# aardvark-dns (docs/proposals/own-toolchain.md, phase 1). Three components, not the
# combined installer: the compiler, cargo and the standard library are all a build needs,
# and the combined tarball adds documentation, clippy, rustfmt and rust-analyzer.
#
# Pinned like go.sh: VERSION and one SHA-256 per component and architecture, written by
# tools/bump-toolchain.sh after each tarball verified against rust-key.asc (the Rust
# release signing key, which signs the .asc beside every file in /dist).
VERSION="1.100.0"
LICENSE="MIT OR Apache-2.0"
URL_BASE="https://static.rust-lang.org/dist"
COMPONENTS="rustc cargo rust-std"

# toolchain_file <component> <arch>: the file name under URL_BASE.
toolchain_file() {
    local triple
    case "$2" in
        amd64) triple=x86_64-unknown-linux-gnu ;;
        arm64) triple=aarch64-unknown-linux-gnu ;;
        *) echo "error: no Rust triple for $2" >&2; return 1 ;;
    esac
    echo "$1-${VERSION}-${triple}.tar.gz"
}

SHA256_rustc_amd64="67b0e85ff4b3eee83c48b8ae1da10dd0bcd60b43d02cd0e7df78c59d19f6b960"
SHA256_rustc_arm64="7ccc960846f81e016086f1931dedadd837a3317da80e0114df68651145c0f43b"
SHA256_cargo_amd64="4e7fc3cbe98e23c88a3c6ce37f834e3ffbc91536789782c4c4a9670df6b16a49"
SHA256_cargo_arm64="b3acccbba065da7e97c20592442f104bd9554d54bf87a323bba8a1d6e61282eb"
SHA256_rust_std_amd64="61eb09d68b338e230d698716927bd4623049c9910ef4a4972e66d763d66ed9e1"
SHA256_rust_std_arm64="89b01323a6611c9382a401282fb66eb80f1f3241df3e139d3df41afbd2da314b"
