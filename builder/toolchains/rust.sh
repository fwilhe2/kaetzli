# Rust, from upstream's stable release rather than sid's rustc and cargo, for netavark and
# aardvark-dns (docs/proposals/own-toolchain.md, phase 1). Three components, not the
# combined installer: the compiler, cargo and the standard library are all a build needs,
# and the combined tarball adds documentation, clippy, rustfmt and rust-analyzer.
#
# Pinned like go.sh: VERSION and one SHA-256 per component and architecture, written by
# tools/bump-toolchain.sh after each tarball verified against rust-key.asc (the Rust
# release signing key, which signs the .asc beside every file in /dist).
VERSION="1.100.1"
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

SHA256_rustc_amd64="11c38d8d8e935a7ad871227d51053eb7f8d0a9e3c998b6f500d7ad35eb4120d9"
SHA256_rustc_arm64="c7d99abaedc213b0d0f2671d1fab706547e54f88f1682d62d777f2cca80fd751"
SHA256_cargo_amd64="965d154ea193f8f446482b627bf636690923ea05acebc95a54ffc9545065ed96"
SHA256_cargo_arm64="f5093f119f1390caea4acbc7d97476e53b88f959964ebfe57718773c7ed693b7"
SHA256_rust_std_amd64="ceaa1fd4662ec04a1d3008f3d4bfe9f781773ae6e25eedb3419b348a72a9db40"
SHA256_rust_std_arm64="49d26d5591cb129588397dfb4fd3156206a9f5ab8323bb189533536f0fc88ba0"
