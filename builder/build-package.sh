#!/bin/bash
# Container entrypoint for every package build: set up the compiler flags and helpers,
# then source the package's build.sh.
#
# Bind mounts set up by ../build.sh:
#   /usr/local/src      unpacked source tree (working directory)
#   /package-build.sh   the package's build.sh
#   /usr/local/rootfs   this package's staging tree, empty at the start (DESTDIR)
#   /usr/local/sysroot  our glibc, compiled against as $SYSROOT
set -euo pipefail

ROOTFS=/usr/local/rootfs

# Merged /usr, with sbin merged into bin, in place before anything installs: systemd
# taints the system ("unmerged-usr", "unmerged-bin") otherwise.
install -d "$ROOTFS"/usr/{bin,lib}
ln -sfn bin      "$ROOTFS/usr/sbin"
ln -sfn usr/bin  "$ROOTFS/bin"
ln -sfn usr/sbin "$ROOTFS/sbin"
ln -sfn usr/lib  "$ROOTFS/lib"
ln -sfn usr/lib  "$ROOTFS/lib64"

# Compile and link against our glibc rather than the builder's, so binaries do not ask
# for symbol versions only sid has. --sysroot points the default paths at ours, and the
# builder's directories are appended after it (-idirafter, trailing -L) so every other
# header and library is still Debian's. docs/guides/linking.md has the long version.
if [ -n "${SYSROOT:-}" ]; then
    multiarch=$(gcc -print-multiarch)

    sysroot_cppflags="--sysroot=$SYSROOT"
    sysroot_cppflags+=" -idirafter /usr/include/$multiarch -idirafter /usr/include"

    # Ours named first, explicitly: a -L outranks the sysroot's defaults, so appending
    # Debian's alone would hand -lc back to sid. -B for the crt files, which gcc finds
    # relative to itself rather than the sysroot. -rpath-link resolves the libraries'
    # own dependencies at link time only.
    sysroot_ldflags="--sysroot=$SYSROOT"
    for dir in /usr/lib64 /usr/lib; do
        [ -d "$SYSROOT$dir" ] || continue
        sysroot_ldflags+=" -B$SYSROOT$dir -L$SYSROOT$dir -Wl,-rpath-link,$SYSROOT$dir"
    done
    sysroot_ldflags+=" -L/usr/lib/$multiarch -L/usr/lib"
    # -rpath-link for Debian's too: a library's own dependencies (libpam needing
    # libaudit) are otherwise looked up in the sysrooted defaults.
    sysroot_ldflags+=" -Wl,-rpath-link,/usr/lib/$multiarch -Wl,-rpath-link,/usr/lib"

    # Hardening, the set Fedora builds every package with. Debian's gcc defaults to PIE
    # and nothing else — the rest comes from dpkg-buildflags, which is not here — so
    # without these lines a package had no stack protector and a writable GOT.
    #   _FORTIFY_SOURCE=3   bounds-checked libc calls where the size is known; -U first,
    #                       so a build system's own -D is not a redefinition
    #   stack-protector     a canary in every function with an array or an address taken
    #   stack-clash         stack probes, so a large alloca cannot jump the guard page
    #   cf-protection, branch-protection
    #                       endbr64/BTI landing pads and SHSTK/PAC markings; inert until
    #                       every object linked in has them, glibc's start files included
    #   relro, now          the GOT read-only once the loader has resolved everything
    # In CFLAGS, not CPPFLAGS: gcc's target libraries (packages/gcc) are built from
    # CPPFLAGS alone, and libgcc_s must not call a stack protector. glibc and the kernel
    # are NO_SYSROOT and never see these. test/check-hardening.py holds every package's
    # binaries to them.
    hardening="-U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=3 -fstack-protector-strong -fstack-clash-protection"
    case "$(uname -m)" in
        x86_64)  hardening+=" -fcf-protection=full" ;;
        aarch64) hardening+=" -mbranch-protection=standard" ;;
    esac

    export CPPFLAGS="$sysroot_cppflags${CPPFLAGS:+ $CPPFLAGS}"
    export LDFLAGS="$sysroot_ldflags -Wl,-z,relro -Wl,-z,now${LDFLAGS:+ $LDFLAGS}"

    # -O2 has to be said: autoconf only defaults to it when CFLAGS is unset, and setting
    # it is what the line above does, so everything was silently -O0. No -g, since the
    # image is stripped. SYSROOT_CFLAGS is the same without -O2, for meson, whose c_args
    # come after its buildtype's optimization (see meson_install). C++ also gets
    # libstdc++'s cheap precondition checks (operator[] in bounds, front() of a non-empty
    # container), as Fedora's builds do.
    export SYSROOT_CFLAGS="$sysroot_cppflags $hardening"
    export SYSROOT_CXXFLAGS="$SYSROOT_CFLAGS -D_GLIBCXX_ASSERTIONS"
    export CFLAGS="$sysroot_cppflags -O2 $hardening${CFLAGS:+ $CFLAGS}"
    export CXXFLAGS="$sysroot_cppflags -O2 $hardening -D_GLIBCXX_ASSERTIONS${CXXFLAGS:+ $CXXFLAGS}"
fi

# Go and Rust read none of the above, so they get the sysroot their own way, are pinned
# offline (the build has no network), and keep paths out of the binary (reproducible).
# -buildmode=pie: Go's default on linux is a fixed-address executable, which ASLR cannot
# move; a cgo program links through gcc with the LDFLAGS above, so it is RELRO and
# BIND_NOW as well. A CGO_ENABLED=0 program meant to stay static says -buildmode=exe on
# its own command line, which overrides this.
export GOTOOLCHAIN=local GOPROXY=off GOFLAGS="-mod=vendor -trimpath -buildvcs=false -buildmode=pie"
export GOPATH=/tmp/go GOCACHE=/tmp/go-cache CGO_ENABLED=1
export CGO_CFLAGS="${CFLAGS:-}" CGO_LDFLAGS="${LDFLAGS:-}"
# Rust: one -C link-arg per flag, because cargo splits RUSTFLAGS on whitespace and rustc
# rejects a bare -B.
export CARGO_HOME=/tmp/cargo CARGO_NET_OFFLINE=true
RUSTFLAGS="--remap-path-prefix=/usr/local/src=."
for flag in ${LDFLAGS:-}; do RUSTFLAGS+=" -C link-arg=$flag"; done
export RUSTFLAGS

# cargo_vendored <cargo args>...
#
# cargo build --release, offline and --locked, against ./vendor (from VENDOR_TARBALL).
# LTO with one codegen unit, which drops most of the dependency tree from the binary.
cargo_vendored() {
    install -d "$CARGO_HOME"
    cat > "$CARGO_HOME/config.toml" <<CARGO
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "$PWD/vendor"

[profile.release]
lto = true
codegen-units = 1
CARGO
    cargo build --release --offline --locked "$@"
}

# The helpers every packages/<pkg>/build.sh can use; it is sourced, so they are in scope.

# A plain `make` is parallel.
export MAKEFLAGS="-j$(nproc)"

# drop_installed <name-in-usr/bin | path/under/rootfs>...
#
# Delete what `make install` put there, and fail if one was not installed: a removal that
# silently does nothing after an upstream rename is how an interpreter script ships.
drop_installed() {
    local prog f
    for prog in "$@"; do
        case "$prog" in
            */*) f="$ROOTFS/$prog" ;;
            *)   f="$ROOTFS/usr/bin/$prog" ;;
        esac
        if [ ! -e "$f" ] && [ ! -L "$f" ]; then
            echo "error: $prog is not installed — this removal list is stale" >&2
            return 1
        fi
        rm -rf "$f"
    done
}

# assert_not_linked <library-substring> <path-under-rootfs>...
#
# Fail if an installed binary has the library in DT_NEEDED: for a dependency configured
# out that could come back without a word.
assert_not_linked() {
    local lib="$1" bin
    shift
    for bin in "$@"; do
        if readelf -d "$ROOTFS/$bin" | grep -q "NEEDED.*$lib"; then
            echo "error: $bin is linked against $lib, which this package configures out" >&2
            return 1
        fi
    done
}

# meson_install [meson setup options]...
#
# setup, compile and install. --buildtype=release because meson's default is -O0, and
# CFLAGS without the -O2 so the buildtype decides. -Dlibdir=lib, not the multiarch path.
meson_install() {
    CFLAGS="${SYSROOT_CFLAGS:-}" CXXFLAGS="${SYSROOT_CXXFLAGS:-}" \
        meson setup --prefix /usr --buildtype=release -Dlibdir=lib "$@" build
    meson compile -C build
    meson install -C build --destdir "$ROOTFS"
}

cd /usr/local/src
source /package-build.sh

# The license texts, into usr/share/licenses/<pkg>/ under the path each has in the
# source: `make install` puts almost none of them anywhere, and the few it does land in
# usr/share/doc, which the image trims. Upstream's own (top level, doc/, any LICENSES/
# directory as REUSE and the kernel lay them out, debian/copyright for Debian's tarballs)
# and every vendored dependency's, since MIT and Apache-2.0 code linked into a Go or Rust
# binary ships its notice with it. packages/<pkg>/licenses/ is for a source that has no
# text to copy. Identical files are hardlinked, so a hundred copies of Apache-2.0 cost
# one. A package with no license text at all fails here rather than ship without one.
if [ -n "${KAETZLI_PKG:-}" ]; then
    licenses=$ROOTFS/usr/share/licenses/$KAETZLI_PKG
    name=( \( -iname 'copying*' -o -iname 'licen[cs]e*' -o -iname 'notice*' -o -iname 'copyright*'
              -o -iname 'unlicense*' \) )
    code=( \( -name '*.[ch]' -o -name '*.go' -o -name '*.rs' -o -name '*.py' -o -name '*.json'
              -o -name '*.toml' -o -name '*.y*ml' -o -name '*.in' -o -name '*.am' -o -name '*.sh'
              -o -name '*.pl' -o -name '*.html' -o -name '*.xml' \) )
    found=$(cd /usr/local/src && find . -xtype f \( \
            \( ! -path './*/*' "${name[@]}" \) \
            -o \( \( -path './doc/*' -o -path './docs/*' \) ! -path './*/*/*' "${name[@]}" \) \
            -o -path '*/LICENSES/*' -o -path '*/licenses/*' \
            -o -path './debian/copyright' \
            -o \( -path './vendor/*' "${name[@]}" \) \
        \) ! "${code[@]}" \
        ! -path '*/test/*' ! -path '*/tests/*' ! -path '*/testdata/*' ! -path '*/testsuite/*' \
        | LC_ALL=C sort)
    if [ -n "$found" ]; then
        install -d "$licenses"
        (cd /usr/local/src && xargs -d '\n' cp -L --parents -t "$licenses" <<< "$found")
    fi
    if [ -d /usr/local/licenses ]; then
        install -d "$licenses"
        cp -L -r /usr/local/licenses/. "$licenses"/
    fi
    if [ ! -d "$licenses" ] || [ -z "$(find "$licenses" -type f)" ]; then
        echo "error: $KAETZLI_PKG ships no license text: none in its source, and no" >&2
        echo "       packages/$KAETZLI_PKG/licenses/ to stand in for one" >&2
        exit 1
    fi
    chmod -R u=rwX,go=rX "$licenses"
    find "$licenses" -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum \
        | awk 'seen[$1]++ { print first[$1] "\t" substr($0, 67); next } { first[$1] = substr($0, 67) }' \
        | while IFS=$'\t' read -r keep dup; do ln -f "$keep" "$dup"; done
fi

# The component record image/build-rootfs.sh turns into the SBOM: the pins ../build.sh
# passes as KAETZLI_*. After the build, so only a package that installed gets one.
if [ -n "${KAETZLI_PKG:-}" ]; then
    components=$ROOTFS/usr/share/kaetzli/components
    install -d "$components"
    {
        printf 'name=%s\n'    "$KAETZLI_PKG"
        printf 'version=%s\n' "${KAETZLI_VERSION:-}"
        printf 'license=%s\n' "${KAETZLI_LICENSE:-NOASSERTION}"
        printf 'origin=%s\n'  "${KAETZLI_ORIGIN:-tarball}"
        printf 'builder=%s\n' "${KAETZLI_BUILDER:-}"
        # `if`, not `&&`: a false && list would fail the group under set -e.
        if [ -n "${KAETZLI_URL:-}" ];    then printf 'url=%s\n'    "$KAETZLI_URL";    fi
        if [ -n "${KAETZLI_SHA256:-}" ]; then printf 'sha256=%s\n' "$KAETZLI_SHA256"; fi
        if [ -n "${KAETZLI_CPE:-}" ];    then printf 'cpe=%s\n'    "$KAETZLI_CPE";    fi
        if [ -n "${KAETZLI_PATCHES:-}" ]; then printf 'patches=%s\n' "$KAETZLI_PATCHES"; fi
        # One fix=<cve> <patch> <locator> per CVE a patch fixes (tools/patch-cves.sh).
        if [ -n "${KAETZLI_FIXES:-}" ]; then printf '%s\n' "$KAETZLI_FIXES" | tr ';' '\n' | sed 's/^/fix=/'; fi
    } > "$components/$KAETZLI_PKG"
    # Vendored dependencies are a component of their own, with their own checksum.
    if [ -n "${KAETZLI_VENDOR_URL:-}" ]; then
        {
            # Its own version where it has one (libpcap in tcpdump, VENDOR_VERSION); a Rust
            # package's crates are versioned with the package.
            printf 'name=%s\n'    "$KAETZLI_PKG-vendor"
            printf 'version=%s\n' "${KAETZLI_VENDOR_VERSION:-${KAETZLI_VERSION:-}}"
            printf 'license=%s\n' "${KAETZLI_LICENSE:-NOASSERTION}"
            printf 'origin=tarball\n'
            printf 'builder=%s\n' "${KAETZLI_BUILDER:-}"
            printf 'url=%s\n'     "$KAETZLI_VENDOR_URL"
            printf 'sha256=%s\n'  "${KAETZLI_VENDOR_SHA256:-}"
        } > "$components/$KAETZLI_PKG-vendor"
    fi

    # What a Go or Rust program compiled in, as purls, one per line in
    # usr/share/kaetzli/modules/<pkg>: the SBOM lists each as a package its program
    # CONTAINS, which is what a vulnerability scanner matches advisories against — most of
    # them, for this image, are about a module or crate rather than a program.
    #   Go: each binary's build info (`go version -m`, which reads it without running
    #   anything), its modules after replacements, and its toolchain as stdlib.
    #   Rust: what the build links, from `cargo tree` over the normal dependency edges —
    #   not Cargo.lock, which also lists build and dev dependencies that never ship.
    modules=$(
        find "$ROOTFS" -type f -perm -u+x -print0 | xargs -0 -r -n1 go version -m 2>/dev/null |
            awk -F'\t' '
                # "<file>: go1.25.3" heads each binary; a "=>" line replaces the dep above it.
                /^[^\t].*: go[0-9]/ { flush(); sub(/.*: go/, ""); print "pkg:golang/stdlib@" $0; next }
                $2 == "dep" { flush(); dep = $3 "@" $4; next }
                $2 == "=>"  { dep = $3 "@" $4; next }
                function flush() { if (dep != "") print "pkg:golang/" dep; dep = "" }
                END { flush() }'
        if [ -f "$CARGO_HOME/config.toml" ]; then
            (cd /usr/local/src && cargo tree --offline --locked -e normal --prefix none --format '{p}' 2>/dev/null) |
                awk '$2 ~ /^v[0-9]/ && $3 !~ /^\(\// { print "pkg:cargo/" $1 "@" substr($2, 2) }'
        fi
    )
    if [ -n "$modules" ]; then
        install -d "$ROOTFS/usr/share/kaetzli/modules"
        LC_ALL=C sort -u <<< "$modules" > "$ROOTFS/usr/share/kaetzli/modules/$KAETZLI_PKG"
    fi
fi
