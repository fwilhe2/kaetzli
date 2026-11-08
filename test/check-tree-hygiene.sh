#!/usr/bin/env bash
# Fail an assembled image tree that has a path anyone may write, a symlink to nothing, or
# a script for an interpreter other than bash:
#
#     ./test/check-tree-hygiene.sh <tree>
#
# image/build-rootfs.sh runs it on each flavour's tree once nothing else will change it
# (it is copied into the assembly image beside it); test/selftest.sh runs it on fixtures.
# From Flatcar's cl.filesystem test, which sweeps a booted root the same way.
#
# World-writable: a file or a directory without the sticky bit, which every user may
# change. The disk image is read-only, but the OCI flavour is not, and there it is
# writable by every user in the container.
#
# Dangling: a symlink whose target is not in the tree. Resolved against the tree, a hop
# at a time, because readlink -f and find -xtype follow an absolute link into the root
# this runs in. A target under a filesystem that is mounted at boot or by the runtime —
# dev, proc, sys, run, tmp, var, all shipped empty — cannot be judged here and is not:
# /etc/resolv.conf into /run, a masked unit to /dev/null, /home into var/.
#
# Interpreted: constraint 5 (CLAUDE.md), bash is the image's one interpreter. By the #!
# line of every file, executable or not, so a script named without an extension is seen
# (iproute2's routel is python, OpenSSL's c_rehash perl), which interpreters.txt's
# extensions cannot. sh and bash are allowed, directly or through env, and nft, whose -f
# rulesets are configuration rather than a language. A make install that brings one is
# fixed in that package's build.sh (drop_installed), with an absent line.
set -euo pipefail

tree="${1:?usage: $0 <tree>}"
[ -d "$tree" ] || { echo "error: $tree is not a directory" >&2; exit 1; }
tree=$(cd "$tree" && pwd)
status=0

writable=$(cd "$tree" && find . -xdev ! -type l -perm -0002 ! \( -type d -perm -1000 \) -printf '%m %P\n')
if [ -n "$writable" ]; then
    echo "error: world-writable paths in $tree (mode, path):" >&2
    printf '%s\n' "$writable" | sed 's/^/  /' >&2
    status=1
fi

# The path <rel> names, every symlink in it followed inside the tree; fails on a loop.
resolve() { # rel
    local rest=$1 done='' comp next target hops=0
    while [ -n "$rest" ]; do
        comp=${rest%%/*}
        if [ "$comp" = "$rest" ]; then rest=''; else rest=${rest#*/}; fi
        case "$comp" in
            ''|.) continue ;;
            ..)   case "$done" in */*) done=${done%/*} ;; *) done='' ;; esac; continue ;;
        esac
        next=${done:+$done/}$comp
        if [ -L "$tree/$next" ]; then
            hops=$((hops + 1))
            [ "$hops" -le 40 ] || return 1
            target=$(readlink "$tree/$next")
            case "$target" in
                /*) done=''; rest="${target#/}${rest:+/$rest}" ;;
                *)  rest="$target${rest:+/$rest}" ;;
            esac
        else
            done=$next
        fi
    done
    printf '%s\n' "$done"
}

dangling=()
while IFS= read -r -d '' link; do
    if ! path=$(resolve "$link"); then
        dangling+=("$link -> $(readlink "$tree/$link") (a loop)")
        continue
    fi
    case "$path" in
        dev|dev/*|proc|proc/*|sys|sys/*|run|run/*|tmp|tmp/*|var|var/*) continue ;;
    esac
    [ -e "$tree/$path" ] || dangling+=("$link -> $(readlink "$tree/$link")")
done < <(cd "$tree" && find . -xdev -type l -printf '%P\0')
if [ "${#dangling[@]}" -gt 0 ]; then
    echo "error: dangling symlinks in $tree (fix or delete them where they are installed):" >&2
    printf '  %s\n' "${dangling[@]}" >&2
    status=1
fi

# The interpreter is the #! line's first word, or after env the first that is not an
# option; judged by its name, wherever it lives.
# shellcheck disable=SC2016 # an awk program, in awk's own $ syntax
interpreted=$(cd "$tree" && find . -xdev -type f -exec env LC_ALL=C awk '
    FNR == 1 {
        if (substr($0, 1, 2) == "#!") {
            line = substr($0, 3); sub(/^[ \t]+/, "", line)
            n = split(line, w, /[ \t]+/); i = w[1]
            if (i ~ /(^|\/)env$/) { k = 2; while (k <= n && w[k] ~ /^-/) k++; i = w[k] }
            b = i; sub(/.*\//, "", b)
            if (b != "sh" && b != "bash" && b != "nft") {
                f = FILENAME; sub(/^\.\//, "", f); print f ": #!" line
            }
        }
        nextfile
    }' {} +)
if [ -n "$interpreted" ]; then
    echo "error: scripts for an interpreter the image does not ship, in $tree (bash is the" >&2
    echo "       one interpreter, CLAUDE.md constraint 5; drop them where they are installed):" >&2
    printf '%s\n' "$interpreted" | sed 's/^/  /' >&2
    status=1
fi

[ "$status" = 0 ] && echo "$tree: nothing world-writable, no dangling symlinks, no other interpreter's scripts"
exit "$status"
