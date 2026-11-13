# Go, from upstream's release tarballs rather than sid's golang-go, so podman, kubernetes,
# CRI-O and the CNI plugins build as soon as they require a new Go, not once Debian has
# uploaded it (docs/proposals/own-toolchain.md, phase 1).
#
# Pinned like a package: VERSION and the SHA-256 of each architecture's tarball, written
# together by tools/bump-toolchain.sh after the tarball verified against go-key.asc
# (Google's Linux packages signing key, which signs the .asc beside every Go download).
# tools/check-toolchains.py proposes new releases; tools/prep.sh fetches, checks and hands
# the tarballs to builder/Containerfile, and this file is in the builder tag.
VERSION="1.27.4"
LICENSE="BSD-3-Clause"
URL_BASE="https://dl.google.com/go"
COMPONENTS="go"

# toolchain_file <component> <arch>: the file name under URL_BASE.
toolchain_file() { echo "go${VERSION}.linux-$2.tar.gz"; }

SHA256_go_amd64="7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840"
SHA256_go_arm64="3392582988a273a87ab5043ee1b2c9c17a2c3c00580bf9326c47a2e0aaaeac92"
