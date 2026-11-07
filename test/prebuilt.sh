#!/usr/bin/env bash
# Run binaries this repository did not build:
#
#     ./test/prebuilt.sh                        # compile, then run in output/kaetzli-oci.tar
#     ./test/prebuilt.sh build [dir]            # compile only, into dir (output/prebuilt)
#     ./test/prebuilt.sh oci [dir] [archive]    # run what dir holds in the OCI image
#     ./test/prebuilt.sh ssh [dir]              # ...or in the guest $SSH_DIR's config reaches
#
# Every other check here runs something compiled in builder/, against our sysroot. That
# leaves out the most common way anything gets onto a general-purpose Linux — somebody
# else's toolchain, on somebody else's distribution — and it is a different question,
# because a prebuilt binary asks for whatever *its* toolchain links by default rather
# than whatever our configure lines allowed. Until packages/gcc existed, the answer for
# nearly every Rust binary for a *-linux-gnu target was "libgcc_s.so.1: cannot open
# shared object file", and nothing here said so.
#
# So this compiles a handful of small programs on the host, with the host's stock
# compilers, and runs them in the image. Each is chosen for what it needs from the image,
# not for what it does:
#
#   c-cancel     pthread_cancel, which makes glibc dlopen libgcc_s — the case
#                check-rootfs-deps.py cannot see, since nothing has it in NEEDED.
#   cxx          libstdc++ and an exception that has to unwind through it.
#   rust         libgcc_s in NEEDED, and a panic caught with catch_unwind.
#   go-cgo       a dynamically linked Go binary, through glibc's NSS for user lookup,
#                and /usr/share/zoneinfo through time.LoadLocation.
#   go-static    the CGO_ENABLED=0 binary most Go projects publish: zoneinfo, and the
#                CA bundle where crypto/x509 looks for it.
#
# Compiling and running are separate so that CI can compile once, in the rootfs job, and
# run the same bytes in both flavours: in the OCI image there, and over ssh in the boot
# job's guest (test/ssh.py does that when PREBUILT names a directory). The two are not
# redundant. The container is the disk image *minus* the subtractions block, so it is
# the stricter test of whether a library is present — only it would notice a subtraction
# taking libgcc_s. The guest is the image as a VM user gets it, and differs where these
# programs look: its nsswitch.conf puts systemd's NSS modules ahead of files, so
# go-cgo's user lookup takes another path there.
#
# A host compiler that is missing skips its program with a note locally and fails under
# CI, where all four are expected. The host's toolchain also has to be no newer than
# ours: a binary built against a newer glibc, or by a g++ whose libstdc++ defines
# GLIBCXX versions packages/gcc's does not, is correctly refused. That is a coupling worth
# hearing about — it is how this test says packages/gcc wants a bump — but it is a
# statement about the host as much as the image.
set -euo pipefail

cd "$(dirname "$0")/.."

# Compile the five programs into $1. Each one's source is here rather than in a file of
# its own, because each is a few lines and only means anything next to the reason it
# exists.
build() {
    local out work
    mkdir -p "$1"
    # Absolute, because the go build below runs from its own module directory.
    out=$(realpath "$1")
    work=$(mktemp -d)
    missing=()
    echo "compiling on the host, into $out:"

    cat >"$work/cancel.c" <<'SRC'
#include <pthread.h>
#include <stdio.h>
#include <unistd.h>
static void *spin(void *arg) { (void)arg; for (;;) pause(); return NULL; }
int main(void) {
    pthread_t t;
    void *ret;
    pthread_create(&t, NULL, spin, NULL);
    pthread_cancel(t);
    pthread_join(t, &ret);
    if (ret != PTHREAD_CANCELED) return 1;
    puts("cancelled");
    return 0;
}
SRC
    have gcc c-cancel && gcc -O2 -pthread -o "$out/c-cancel" "$work/cancel.c"

    cat >"$work/cxx.cc" <<'SRC'
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
int main() {
    std::string got;
    std::thread t([&] {
        try { throw std::runtime_error("unwound"); }
        catch (const std::exception &e) { got = e.what(); }
    });
    t.join();
    std::cout << got << std::endl;
    return got == "unwound" ? 0 : 1;
}
SRC
    have g++ cxx && g++ -O2 -pthread -o "$out/cxx" "$work/cxx.cc"

    cat >"$work/main.rs" <<'SRC'
fn main() {
    std::panic::set_hook(Box::new(|_| {}));
    let r = std::panic::catch_unwind(|| panic!("unwound"));
    assert!(r.is_err());
    println!("caught");
}
SRC
    have rustc rust && rustc -O -o "$out/rust" "$work/main.rs"

    mkdir -p "$work/go"
    cat >"$work/go/main.go" <<'SRC'
package main

import (
	"crypto/x509"
	"fmt"
	"os"
	"os/user"
	"time"
)

func main() {
	loc, err := time.LoadLocation("Europe/Berlin")
	if err != nil {
		fmt.Println("zoneinfo:", err)
		os.Exit(1)
	}
	u, err := user.Current()
	if err != nil {
		fmt.Println("user:", err)
		os.Exit(1)
	}
	pool, err := x509.SystemCertPool()
	if err != nil || len(pool.Subjects()) == 0 { //nolint:staticcheck
		fmt.Println("certificates: none found", err)
		os.Exit(1)
	}
	fmt.Println(loc, u.Username, len(pool.Subjects()), "roots") //nolint:staticcheck
}
SRC
    printf 'module prebuilt\n\ngo 1.21\n' >"$work/go/go.mod"
    if have go go; then
        # GOFLAGS empty and a private cache, so the host's settings say nothing about it.
        (
            export GOFLAGS= GOCACHE="$work/gocache" GOTOOLCHAIN=local
            cd "$work/go"
            CGO_ENABLED=1 go build -o "$out/go-cgo" .
            CGO_ENABLED=0 go build -o "$out/go-static" .
        )
    fi

    # Go's module cache is read-only on disk, so a plain rm -rf cannot clear it.
    chmod -R u+w "$work"
    rm -rf "$work"

    if [ -n "${CI:-}" ] && [ ${#missing[@]} -gt 0 ]; then
        echo "error: CI is expected to have every host compiler; missing: ${missing[*]}" >&2
        return 1
    fi
}

have() {  # have <command> <program it builds>
    command -v "$1" >/dev/null && return 0
    echo "  skip  $2: no $1 on this host"
    missing+=("$1")
    return 1
}

# Both runners take a directory of binaries, and both restore the executable bit first:
# CI hands the directory from one job to the next as an artifact, and upload-artifact
# does not keep file modes.
binaries() {
    compgen -G "$1/*" >/dev/null || {
        echo "error: no binaries in $1 (compile them with: $0 build $1)" >&2
        return 1
    }
    chmod +x "$1"/*
}

report() {  # report <name> <exit status> <output>
    if [ "$2" -eq 0 ]; then
        echo "  ok    $1: $3"
    else
        echo "  FAIL  $1:"
        printf '        %s\n' "$3"
    fi
}

run_oci() {
    local dir="$1" archive="$2" loaded ref bin name out rc fail=0
    [ -f "$archive" ] || { echo "error: no OCI archive at $archive (see test/oci.sh)" >&2; return 1; }
    binaries "$dir"
    loaded=$(podman load --quiet --input "$archive" | tail -n1)
    ref=${loaded##*: }
    echo "running in $ref:"
    for bin in "$dir"/*; do
        name=$(basename "$bin")
        rc=0
        out=$(podman run --rm --network=none --volume "$(realpath "$dir"):/prebuilt:ro,Z" \
                  --entrypoint "/prebuilt/$name" "$ref" 2>&1) || rc=$?
        report "$name" "$rc" "$out"
        [ "$rc" -eq 0 ] || fail=1
    done
    return $fail
}

# Over the ssh_config qemu_ssh_setup wrote, the one tools/ssh.sh uses — so this runs in
# whichever guest SSH_DIR describes, test/ssh.py's or tools/boot-qemu.py's.
run_ssh() {
    local dir="$1" config="${SSH_DIR:-output/ssh}/config" bin name out rc fail=0
    [ -f "$config" ] || { echo "error: no $config — is a guest running?" >&2; return 1; }
    binaries "$dir"
    ssh -n -F "$config" kaetzli 'rm -rf /tmp/prebuilt'
    scp -q -p -r -F "$config" "$dir" kaetzli:/tmp/prebuilt
    echo "running in the guest, over ssh:"
    for bin in "$dir"/*; do
        name=$(basename "$bin")
        rc=0
        out=$(ssh -n -F "$config" kaetzli "/tmp/prebuilt/$name" 2>&1) || rc=$?
        report "$name" "$rc" "$out"
        [ "$rc" -eq 0 ] || fail=1
    done
    ssh -n -F "$config" kaetzli 'rm -rf /tmp/prebuilt' || true
    return $fail
}

case "${1:-}" in
    build) build "${2:-output/prebuilt}" ;;
    oci)   run_oci "${2:-output/prebuilt}" "${3:-output/kaetzli-oci.tar}" ;;
    ssh)   run_ssh "${2:-output/prebuilt}" ;;
    "")    dir=$(mktemp -d)
           trap 'rm -rf "$dir"' EXIT
           # Two statements, not `build && run_oci`: on the left of && set -e is off for
           # the whole of build(), and a compiler failure would go unnoticed.
           build "$dir"
           run_oci "$dir" output/kaetzli-oci.tar ;;
    *)     echo "usage: $0 [build [dir] | oci [dir] [archive] | ssh [dir]]" >&2
           exit 1 ;;
esac
