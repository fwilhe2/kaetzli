#!/bin/bash
# Prove the static checks can fail:
#
#     ./test/selftest.sh
#
# Every check in test/ that reads a tree reports success when it finds nothing wrong — and
# a check that is broken finds nothing wrong too. That is not hypothetical here. The first
# version of the symbol-resolution check, in bash, resolved NEEDED by SONAME only, decided
# bash's libtinfo.so.6 was missing, skipped bash and seventy other programs, and still
# printed its success line. CLAUDE.md's grep -a story is the same shape: a sweep that matched
# nothing and said so with exit 0.
#
# So each check gets small trees built to be wrong in exactly one way, and has to fail on
# each — and say *what* is wrong, by name, so that a check that crashes or fails for an
# unrelated reason does not count. Each also gets a tree that is right in every way, and
# has to pass on it: a fixture that fails for the wrong reason proves nothing either.
#
# The ELF trees are compiled here, from a few lines of C, with the host's gcc and against
# the host's libc, rather than committed: a binary fixture is opaque in review and only
# runs on one architecture. Nothing here needs the image, the builder or podman, so it
# runs in seconds in its own workflow (.github/workflows/selftest.yml).
set -euo pipefail

cd "$(dirname "$0")/.."

for tool in gcc readelf; do
    command -v "$tool" >/dev/null || { echo "error: $tool is needed to build the fixtures" >&2; exit 1; }
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
failures=0

# expect pass|fail <description> <pattern> <command...>
#
# For "fail", the command must exit non-zero AND print the pattern (stdout or stderr):
# the name of the thing the fixture broke. For "pass", it must exit zero.
expect() {
    local want=$1 what=$2 pattern=$3 out rc=0
    shift 3
    out=$("$@" 2>&1) || rc=$?
    if [ "$want" = pass ] && [ "$rc" -eq 0 ]; then
        echo "ok    $what"
    elif [ "$want" = fail ] && [ "$rc" -ne 0 ] && grep -q -- "$pattern" <<< "$out"; then
        echo "ok    $what"
    else
        echo "FAIL  $what (expected the check to $want${pattern:+ naming '$pattern'}, exit $rc)"
        sed 's/^/        /' <<< "$out" | head -20
        failures=$((failures + 1))
    fi
}

# ---------------------------------------------------------------------------------
# ELF fixtures
#
# libfoo comes in two builds with one SONAME, the way a library and the newer header
# someone compiled against do:
#
#   new  foo_old, foo_new (unversioned), foo_v1 @FOO_1, foo_v2 @FOO_2
#   old  foo_old, foo_v1 @FOO_1 — the release before foo_new and FOO_2 existed
#
# and app is linked against new. A tree with new is correct; a tree with old is each of
# the failures this repository's coupling to the builder's headers can produce
# (docs/guides/linking.md, section 4). app also references a weak foo_maybe that no build
# defines, which is legitimate and must not be reported.

src=$work/src
mkdir -p "$src"

cat > "$src/new.c" <<'EOF'
int foo_old(void) { return 1; }
int foo_new(void) { return 2; }
int foo_v1(void) { return 3; }
int foo_v2(void) { return 4; }
EOF
cat > "$src/new.map" <<'EOF'
FOO_1 { global: foo_v1; };
FOO_2 { global: foo_v2; } FOO_1;
EOF
cat > "$src/old.c" <<'EOF'
int foo_old(void) { return 1; }
int foo_v1(void) { return 3; }
EOF
cat > "$src/old.map" <<'EOF'
FOO_1 { global: foo_v1; };
EOF
# foo_old and foo_new stay unversioned: a version script only tags what it names, and
# the rest of the exports stay as they are, which is what a library without symbol
# versions looks like from the outside.
cat > "$src/app.c" <<'EOF'
int foo_old(void); int foo_new(void); int foo_v1(void); int foo_v2(void);
__attribute__((weak)) int foo_maybe(void);
int main(void) {
    return foo_old() + foo_new() + foo_v1() + foo_v2() + (foo_maybe ? foo_maybe() : 0);
}
EOF

so() { # <out> <c> <map>
    gcc -shared -fPIC -Wl,-soname,libfoo.so.1 -Wl,--version-script="$3" "$2" -o "$1"
}
mkdir -p "$src/new" "$src/old"
so "$src/new/libfoo.so.1" "$src/new.c" "$src/new.map"
so "$src/old/libfoo.so.1" "$src/old.c" "$src/old.map"
gcc "$src/app.c" -L"$src/new" -l:libfoo.so.1 -o "$src/app"

# The host's libc and loader, under the names the binaries ask for, so a tree is complete
# without anything from this repository's build. The loader is whatever app's PT_INTERP
# names: ld-linux-x86-64.so.2 or ld-linux-aarch64.so.1.
interp=$(readelf -lW "$src/app" | sed -n 's/.*Requesting program interpreter: \(.*\)]/\1/p')
libc=$(gcc -print-file-name=libc.so.6)

tree() { # <name> <libfoo build> — a minimal merged-/usr tree
    local t=$work/$1
    mkdir -p "$t/usr/bin" "$t/usr/lib"
    ln -s usr/lib "$t/lib"
    ln -s usr/lib "$t/lib64"
    cp "$src/app" "$t/usr/bin/app"
    cp -L "$libc" "$t/usr/lib/libc.so.6"
    cp -L "$interp" "$t/usr/lib/$(basename "$interp")"
    [ -z "$2" ] || cp "$src/$2/libfoo.so.1" "$t/usr/lib/libfoo.so.1"
    echo "$t"
}

empty=$work/empty-allowlist
: > "$empty"

good=$(tree good new)
expect pass "rootfs-deps: a complete tree passes"            "" ./test/check-rootfs-deps.py "$good" "$empty"
expect pass "symbol-versions: a complete tree passes"        "" ./test/check-symbol-versions.py "$good"
expect pass "symbol-resolution: a complete tree passes"      "" ./test/check-symbol-resolution.py "$good"

nolib=$(tree nolib "")
expect fail "rootfs-deps: a missing library is reported"     libfoo.so.1 ./test/check-rootfs-deps.py "$nolib" "$empty"
allow=$work/allow-libfoo
echo libfoo.so.1 > "$allow"
expect pass "rootfs-deps: an allowlisted library is accepted" "" ./test/check-rootfs-deps.py "$nolib" "$allow"

old=$(tree old old)
expect fail "symbol-versions: a missing version is reported" FOO_2 ./test/check-symbol-versions.py "$old"
expect fail "symbol-resolution: a missing symbol is reported" foo_new ./test/check-symbol-resolution.py "$old"

# A file that starts like ELF but cannot be read: skipping it, as readelf's errors to
# /dev/null once did, is a pass on something nobody looked at. Truncated mid-header.
trunc=$(tree trunc new)
head -c 40 "$src/app" > "$trunc/usr/bin/truncated"
expect fail "rootfs-deps: a truncated ELF is reported"       usr/bin/truncated ./test/check-rootfs-deps.py "$trunc" "$empty"
expect fail "symbol-versions: a truncated ELF is reported"   usr/bin/truncated ./test/check-symbol-versions.py "$trunc"
expect fail "symbol-resolution: a truncated ELF is reported" usr/bin/truncated ./test/check-symbol-resolution.py "$trunc"

# The loader matches NEEDED against file names, and a symlink is one: here libfoo.so.1
# exists only as a link to a file whose name and SONAME are something else. This is the
# libtinfo.so.6 -> libtinfow.so.6 arrangement that check-symbol-resolution.py once got
# wrong, and both checks that resolve NEEDED have to accept it.
alias=$(tree alias "")
gcc -shared -fPIC -Wl,-soname,libfoo-real.so.1 -Wl,--version-script="$src/new.map" \
    "$src/new.c" -o "$alias/usr/lib/libfoo-real.so.1"
ln -s libfoo-real.so.1 "$alias/usr/lib/libfoo.so.1"
expect pass "rootfs-deps: a library found through a symlink name passes" \
    "" ./test/check-rootfs-deps.py "$alias" "$empty"
expect pass "symbol-resolution: a library found through a symlink name passes" \
    "" ./test/check-symbol-resolution.py "$alias"

# And the same arrangement with the *old* library behind the link, which has to fail.
# The pass above cannot tell a check that followed the link from one that gave up on
# app — an object whose library cannot be found is skipped, and a skipped object reports
# nothing, which is exactly the success the pass asked for. That is how the symlink bug
# went unnoticed the first time, and with only the pass, this file did not catch it
# either when the bug was put back on purpose. A finding cannot be produced by skipping.
alias_old=$(tree alias-old "")
gcc -shared -fPIC -Wl,-soname,libfoo-real.so.1 -Wl,--version-script="$src/old.map" \
    "$src/old.c" -o "$alias_old/usr/lib/libfoo-real.so.1"
ln -s libfoo-real.so.1 "$alias_old/usr/lib/libfoo.so.1"
expect fail "symbol-resolution: a missing symbol behind a symlink name is reported" \
    foo_new ./test/check-symbol-resolution.py "$alias_old"

# ---------------------------------------------------------------------------------
# check-hardening.py reads a staging directory, staging/<pkg>/{rootfs,extensions}, and
# names the package. One program with a local array (so the stack protector instruments
# it) and a printf with an argument (which fortify always turns into __printf_chk),
# built with builder/build-package.sh's flags, then with one of them undone per package.
cat > "$src/hardened.c" <<'EOF'
#include <stdio.h>
#include <string.h>
int main(int argc, char **argv) {
    char buf[64];
    strncpy(buf, argc > 1 ? argv[1] : "x", sizeof buf - 1);
    buf[63] = 0;
    printf("%s %d\n", buf, argc);
    return 0;
}
EOF
# shellcheck disable=SC2054 # -Wl,-z,now is one flag, commas and all
hardened=(-O2 -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=3 -fstack-protector-strong -fPIE -pie
          -Wl,-z,relro -Wl,-z,now -Wl,-z,noexecstack)
staged() { # <staging> <pkg> <extra gcc flags>...
    local dir=$1/$2/rootfs/usr/bin
    mkdir -p "$dir"
    gcc "${hardened[@]}" "${@:3}" "$src/hardened.c" -o "$dir/$2" 2>/dev/null
}
hstage=$work/hardening-good
staged "$hstage" good
expect pass "hardening: a package built with every flag passes" "" \
    ./test/check-hardening.py "$hstage" "$empty"
while read -r pkg finding flags; do
    cp -a "$hstage" "$work/hardening-$pkg"
    # shellcheck disable=SC2086 # the flags are words
    staged "$work/hardening-$pkg" "$pkg" $flags
    expect fail "hardening: $finding is reported, with its package" "$pkg: $finding" \
        ./test/check-hardening.py "$work/hardening-$pkg" "$empty"
done <<'EOF'
lazy      bind-now        -Wl,-z,lazy
norelro   relro           -Wl,-z,norelro
nopie     pie             -fno-PIE -no-pie
execstack exec-stack      -Wl,-z,execstack
nossp     stack-protector -fno-stack-protector
nofortify fortify         -U_FORTIFY_SOURCE
EOF
cp -a "$hstage" "$work/hardening-trunc"
mkdir -p "$work/hardening-trunc/trunc/rootfs/usr/bin"
head -c 40 "$hstage/good/rootfs/usr/bin/good" > "$work/hardening-trunc/trunc/rootfs/usr/bin/trunc"
expect fail "hardening: a truncated ELF is reported, with its package" trunc/rootfs/usr/bin/trunc \
    ./test/check-hardening.py "$work/hardening-trunc" "$empty"

# ---------------------------------------------------------------------------------
# Kernel config
#
# A config that has every symbol kernel-caps.py requires, generated from its list,
# test/kernel-caps.txt, so that adding a requirement there does not break this — and then
# the same config with one removed. Every line that starts with a symbol, both
# architectures' sections included, which is harmless: extra =y lines are never an error.

kernel=$work/kernel
mkdir -p "$kernel/boot"
{
    echo "# Linux/x86 0.0.0 Kernel Configuration"
    awk '/^[A-Z0-9_]+ / { print "CONFIG_" $1 "=y" }' test/kernel-caps.txt
} > "$kernel/boot/config"
expect pass "kernel-caps: a config with every requirement passes" "" ./test/kernel-caps.py "$kernel"

sed -i '/^CONFIG_USER_NS=y$/d' "$kernel/boot/config"
expect fail "kernel-caps: a missing requirement is reported" CONFIG_USER_NS ./test/kernel-caps.py "$kernel"

# kernel-parity.py: two configs that differ only in what may differ (OF, a symbol only
# one arch defines), then a pair that differs in a feature, and in a string value.
parity=$work/parity
mkdir -p "$parity"
printf '%s\n' CONFIG_INET_DIAG=y CONFIG_OF=y 'CONFIG_LSM="yama,bpf"' CONFIG_ARM64_ONLY=y > "$parity/arm64"
printf '%s\n' CONFIG_INET_DIAG=y '# CONFIG_OF is not set' 'CONFIG_LSM="yama,bpf"' CONFIG_X86_ONLY=y > "$parity/amd64"
expect pass "kernel-parity: configs that differ only where an arch may pass" "" \
    ./test/kernel-parity.py "$parity/amd64" "$parity/arm64"
sed 's/^CONFIG_INET_DIAG=y$/# CONFIG_INET_DIAG is not set/' "$parity/amd64" > "$parity/amd64-nodiag"
expect fail "kernel-parity: a feature one arch lacks is reported" CONFIG_INET_DIAG \
    ./test/kernel-parity.py "$parity/amd64-nodiag" "$parity/arm64"
sed 's/^CONFIG_LSM=.*/CONFIG_LSM="selinux,yama,bpf"/' "$parity/amd64" > "$parity/amd64-lsm"
expect fail "kernel-parity: a value that differs is reported" CONFIG_LSM \
    ./test/kernel-parity.py "$parity/amd64-lsm" "$parity/arm64"

# ---------------------------------------------------------------------------------
# check-image-files.sh reads a manifest, not a tree. One rule of each kind, one of them
# for the disk flavour only, and manifests that each break one rule.
rules=$work/image-files
mkdir -p "$rules"
cat > "$rules/fixture.txt" <<'EOF'
# fixture rules
absent        usr/lib/gone/*
present       usr/share/kaetzli/sbom.json
present:erofs boot/config
EOF
# manifest <name> <flavour> <path>...: writes $work/<name>.txt
manifest() {
    local name=$1 flavour=$2
    shift 2
    { echo "# flavour: $flavour"; printf '%s\n' "$@"; } > "$work/$name.txt"
}
manifest good-erofs erofs usr usr/share/kaetzli/sbom.json boot boot/config
manifest good-oci oci usr usr/share/kaetzli/sbom.json
manifest extra erofs usr usr/share/kaetzli/sbom.json boot boot/config usr/lib/gone/deep/file
manifest missing erofs usr boot boot/config usr/share/kaetzli/other.json
manifest noconfig erofs usr usr/share/kaetzli/sbom.json
good_erofs=$work/good-erofs.txt
expect pass "image-files: a manifest that keeps every rule passes" "" \
    env RULES="$rules" ./test/check-image-files.sh "$good_erofs"
expect pass "image-files: a rule for the other flavour is not applied" "" \
    env RULES="$rules" ./test/check-image-files.sh "$work/good-oci.txt"
expect fail "image-files: an absent path that is there is reported" usr/lib/gone/deep/file \
    env RULES="$rules" ./test/check-image-files.sh "$work/extra.txt"
expect fail "image-files: a present path that is missing is reported" usr/share/kaetzli/sbom.json \
    env RULES="$rules" ./test/check-image-files.sh "$work/missing.txt"
expect fail "image-files: a flavour's own rule is applied" boot/config \
    env RULES="$rules" ./test/check-image-files.sh "$work/noconfig.txt"
printf 'usr\nusr/share/kaetzli/sbom.json\n' > "$work/manifest-unlabelled.txt"
expect fail "image-files: a manifest that names no flavour is refused" flavour \
    env RULES="$rules" ./test/check-image-files.sh "$work/manifest-unlabelled.txt"

# ---------------------------------------------------------------------------------
# check-tree-hygiene.sh: a tree with every kind of link it must accept — relative and
# absolute, through a linked directory, into the boot-time filesystems — and a sticky
# world-writable directory; then copies broken one way each. The absolute link's target
# is one the host has too (/usr/bin/env), so a check that resolves against the host's
# root instead of the tree passes the dangling fixture and fails here.
hyg=$work/hygiene-good
mkdir -p "$hyg/usr/bin" "$hyg/usr/lib/real" "$hyg/etc" "$hyg/tmp"
touch "$hyg/usr/bin/tool" "$hyg/usr/lib/real/libx.so.1"
chmod 1777 "$hyg/tmp"
ln -s tool "$hyg/usr/bin/alias"
ln -s /usr/bin/tool "$hyg/usr/bin/absolute"
ln -s real "$hyg/usr/lib/dir"
ln -s ../lib/dir/libx.so.1 "$hyg/usr/bin/through-dir"
ln -s /run/systemd/resolve/stub-resolv.conf "$hyg/etc/resolv.conf"
ln -s /dev/null "$hyg/etc/masked.service"
ln -s var/home "$hyg/home"
# Every #! the image may have: the shell under either path, through env (with -S), and
# nftables' rulesets, which are configuration rather than a language.
printf '#!/bin/sh\n'                  > "$hyg/usr/bin/a-sh"
printf '#!/usr/bin/bash -e\n'         > "$hyg/usr/bin/a-bash"
printf '#!/usr/bin/env bash\n'        > "$hyg/usr/bin/an-env-bash"
printf '#! /usr/bin/env -S bash -e\n' > "$hyg/usr/bin/an-env-s-bash"
printf '#!/usr/sbin/nft -f\n'         > "$hyg/etc/rules.nft"
chmod 755 "$hyg"/usr/bin/a-* "$hyg"/usr/bin/an-*
expect pass "tree-hygiene: a tree with only resolvable links, a sticky /tmp and bash scripts passes" "" \
    ./test/check-tree-hygiene.sh "$hyg"

cp -a "$hyg" "$work/hygiene-file"
chmod 666 "$work/hygiene-file/usr/bin/tool"
expect fail "tree-hygiene: a world-writable file is reported" usr/bin/tool \
    ./test/check-tree-hygiene.sh "$work/hygiene-file"
cp -a "$hyg" "$work/hygiene-dir"
mkdir -m 777 "$work/hygiene-dir/usr/share"
chmod 777 "$work/hygiene-dir/usr/share"
expect fail "tree-hygiene: a world-writable directory without the sticky bit is reported" usr/share \
    ./test/check-tree-hygiene.sh "$work/hygiene-dir"
cp -a "$hyg" "$work/hygiene-dangling"
ln -s /usr/bin/env "$work/hygiene-dangling/usr/bin/host-only"
expect fail "tree-hygiene: an absolute link to a path only the host has is reported" usr/bin/host-only \
    ./test/check-tree-hygiene.sh "$work/hygiene-dangling"
cp -a "$hyg" "$work/hygiene-loop"
ln -s b "$work/hygiene-loop/usr/bin/a"
ln -s a "$work/hygiene-loop/usr/bin/b"
expect fail "tree-hygiene: a symlink loop is reported" "usr/bin/a" \
    ./test/check-tree-hygiene.sh "$work/hygiene-loop"
# Another interpreter, by content: named without an extension (iproute2's routel is
# python, OpenSSL's c_rehash perl), so test/image-files/interpreters.txt cannot see it.
while read -r name shebang; do
    cp -a "$hyg" "$work/hygiene-$name"
    printf '%s\nprint(1)\n' "$shebang" > "$work/hygiene-$name/usr/bin/$name"
    chmod 755 "$work/hygiene-$name/usr/bin/$name"
    expect fail "tree-hygiene: a '$shebang' script is reported" "usr/bin/$name" \
        ./test/check-tree-hygiene.sh "$work/hygiene-$name"
done <<'EOF'
routel   #!/usr/bin/python3
c_rehash #!/usr/bin/env perl
EOF
# Not executable is no excuse: it is still a script for an interpreter nobody ships.
cp -a "$hyg" "$work/hygiene-noexec"
mkdir -p "$work/hygiene-noexec/usr/share/x"
printf '#!/usr/bin/env -S lua\n' > "$work/hygiene-noexec/usr/share/x/helper"
expect fail "tree-hygiene: a non-executable script for another interpreter is reported" \
    usr/share/x/helper ./test/check-tree-hygiene.sh "$work/hygiene-noexec"

# ---------------------------------------------------------------------------------
# check-doc-links.sh: a relative link to a file that is not there, beside every kind of
# link it must not follow — external, anchor-only, with a fragment or a title, and one
# inside a code fence that only looks like a link.
docs=$work/docs-good
mkdir -p "$docs/docs/guides"
echo '# top' > "$docs/README.md"
cat > "$docs/docs/guides/a.md" <<'EOF'
See [the top](../../README.md), [b](b.md#section "B"), [site](https://example.com/x),
[here](#local) and [root-relative](/README.md).

```sh
echo "[not a link](missing-in-fence.md)"
```
EOF
echo '## section' > "$docs/docs/guides/b.md"
expect pass "doc-links: a tree whose links all resolve passes" "" \
    ./test/check-doc-links.sh "$docs"
cp -a "$docs" "$work/docs-broken"
echo 'Moved: [old guide](old-guide.md).' >> "$work/docs-broken/docs/guides/b.md"
expect fail "doc-links: a link to a missing file is reported" old-guide.md \
    ./test/check-doc-links.sh "$work/docs-broken"

echo
if [ "$failures" -gt 0 ]; then
    echo "$failures self-test(s) failed: a check above can no longer tell right from wrong."
    exit 1
fi
echo "Every check fails on what it exists to catch, and passes on a correct tree."
