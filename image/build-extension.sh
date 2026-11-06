#!/bin/bash
# Turn a directory tree into system and configuration extension images for this image
# (docs/guides/sysext.md). Runs in the image-assembly container, like build-rootfs.sh, so that
# an extension is laid out by the same mkfs.erofs with the same reproducibility flags as
# the root it is merged over. The tree is mounted at /usr/local/src and never written to.
#
#     build-extension.sh sysext <name>    the tree is usr/ (and opt/)  → extensions/<name>.raw
#     build-extension.sh confext <name>   the tree is etc/             → confexts/<name>.raw
#     build-extension.sh staged <name>    the tree is extensions/<name>/ as packages with
#                                         EXTENSION=<name> installed into it (build.sh):
#                                         its usr/ and opt/ become the sysext, its etc/ the
#                                         confext, and ELF objects are stripped as in the
#                                         image's own trim
#
# Output goes to /usr/local/output/extensions/ and /usr/local/output/confexts/, named
# after where the images go in a guest, /var/lib/extensions and /var/lib/confexts. The
# file name is the extension's name: systemd-sysext only merges an image whose
# extension-release.<name> matches its file name, so a sysext and a confext that belong
# together share it.
#
# The extension-release file is written here, not taken from the tree, because what it
# says is a promise about this image and not about the extension:
#
#   ID=kaetzli                      only this OS merges it
#   SYSEXT_LEVEL= / CONFEXT_LEVEL=  the level image/files/etc/os-release declares, which
#                                is the ABI an extension built today may rely on. LEVEL
#                                overrides it, which only a test refusing a wrong one wants.
#   ARCHITECTURE=                sysexts only: they carry binaries, so one built here only
#                                runs on this architecture. A confext is text.
#   EXTENSION_RELOAD_MANAGER=1   the merge reloads PID 1, so the units an extension ships
#                                exist as soon as it is merged — at boot that is during
#                                sysinit, and an Upholds= drop-in is what starts them.
#
# Sealed with dm-verity like the root, with the table beside the image as <name>.dm-verity
# (see the end of assemble()).
set -euo pipefail

usage="usage: build-extension.sh sysext|confext|staged <name>"
mode="${1:?$usage}"
name="${2:?$usage}"
[[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "error: '$name' is not a usable extension name" >&2; exit 2; }

# An SPDX document for the extension staged in <tree>: its packages, the Go modules and
# Rust crates they compiled in, and the builders (image/sbom.py).
write_sbom() { # tree out
    local arch
    case "$(uname -m)" in x86_64) arch=amd64 ;; aarch64) arch=arm64 ;; *) arch=$(uname -m) ;; esac
    sbom.py extension "$1" --name "kaetzli-ext-$name-$arch" \
        --created "$(date -u -d "@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y-%m-%dT%H:%M:%SZ)" > "$2"
    echo "sbom-ext-$name.json: $(grep -c '"SPDXID"' "$2") elements"
}

# One image of one class from one tree, which is copied rather than modified.
assemble() { # class tree
    local class="$1" src="$2" release key out allowed top level tree=/tmp/extension
    case "$class" in
        sysext)  release=usr/lib/extension-release.d key=SYSEXT_LEVEL  out=extensions allowed='usr opt' ;;
        confext) release=etc/extension-release.d     key=CONFEXT_LEVEL out=confexts   allowed='etc' ;;
        *) echo "error: class '$class' (expected sysext or confext)" >&2; exit 2 ;;
    esac

    # A sysext merges /usr and /opt and nothing else; anything beside them would be
    # silently ignored in the guest, which is worse than refusing it here.
    for top in "$src"/* "$src"/.[!.]*; do
        [ -e "$top" ] || [ -L "$top" ] || continue
        [[ " $allowed " == *" $(basename "$top") "* ]] || {
            echo "error: a $class carries only $allowed, not /$(basename "$top")" >&2; exit 1; }
    done

    level="${LEVEL:-$(sed -n "s/^$key=//p" /files/etc/os-release)}"
    [ -n "$level" ] || { echo "error: image/files/etc/os-release declares no $key" >&2; exit 1; }

    rm -rf "$tree"
    cp -a "$src" "$tree"
    mkdir -p "$tree/$release"
    {
        echo "ID=kaetzli"
        echo "$key=$level"
        if [ "$class" = sysext ]; then
            case "$(uname -m)" in
                x86_64)  echo "ARCHITECTURE=x86-64" ;;
                aarch64) echo "ARCHITECTURE=arm64" ;;
            esac
        fi
        echo "EXTENSION_RELOAD_MANAGER=1"
    } > "$tree/$release/extension-release.$name"

    # The same construction as the root image (build-rootfs.sh): from a tar stream, so
    # that no xattr the host put on the files — an SELinux label above all — reaches the
    # image, only the file capabilities staged mode applied, with every owner root and
    # every time clamped.
    local erofs_repro=()
    if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
        erofs_repro=(-T "$SOURCE_DATE_EPOCH" --all-time -U 00000000-0000-0000-0000-000000000000)
    fi
    mkdir -p "/usr/local/output/$out"
    tar --create --format=pax --sort=name --numeric-owner --owner=0 --group=0 \
        --xattrs --xattrs-include='security.capability' \
        --mtime="@${SOURCE_DATE_EPOCH:-$(date +%s)}" --clamp-mtime \
        --pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime \
        --directory "$tree" . > /tmp/extension.tar
    rm -f "/usr/local/output/$out/$name.raw"
    mkfs.erofs --quiet -zzstd,15 -C131072 -Eztailpacking,fragments --tar=f "${erofs_repro[@]}" \
        "/usr/local/output/$out/$name.raw" /tmp/extension.tar
    rm -rf "$tree" /tmp/extension.tar

    # dm-verity, as for the root (build-rootfs.sh has the reasoning): the hash tree is
    # appended to the same file, and the table that checks it is written beside the image
    # as <name>.dm-verity, with @DEV@ where the block device goes. A VMM that attaches the
    # image as a disk passes the table on the kernel command line with dm-mod.create=,
    # next to the root's, so the kernel verifies the extension with no userspace involved
    # (qemu_sysexts in test/qemu-lib.sh). One merged from a file in /var/lib/extensions is
    # read without it: that needs systemd's userspace verity, and so libcryptsetup (#191).
    local image="/usr/local/output/$out/$name.raw" size blocks verity hash salt salt_arg=()
    size=$(stat -c %s "$image")
    [ $(( size % 4096 )) -eq 0 ] || { echo "error: $image is not a whole number of 4 KiB blocks" >&2; exit 1; }
    blocks=$(( size / 4096 ))
    if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
        salt_arg=(--salt="$(printf '%s %s %s' "$SOURCE_DATE_EPOCH" "$class" "$name" | sha256sum | cut -c1-64)")
    fi
    verity=$(veritysetup format --no-superblock --hash=sha256 \
        --data-block-size=4096 --hash-block-size=4096 \
        --data-blocks="$blocks" --hash-offset="$size" \
        "${salt_arg[@]}" "$image" "$image")
    hash=$(sed -n 's/^Root hash:[[:space:]]*//p' <<< "$verity")
    salt=$(sed -n 's/^Salt:[[:space:]]*//p' <<< "$verity")
    [ -n "$hash" ] && [ -n "$salt" ] || { echo "error: no root hash and salt from veritysetup for $image" >&2; exit 1; }
    printf '0 %s verity 1 @DEV@ @DEV@ 4096 4096 %s %s sha256 %s %s\n' \
        $(( size / 512 )) "$blocks" "$blocks" "$hash" "$salt" > "/usr/local/output/$out/$name.dm-verity"

    echo "$out/$name.raw: $class, $key=$level, $(du -h "$image" | cut -f1), verity $hash"
}

case "$mode" in
    sysext|confext)
        assemble "$mode" /usr/local/src ;;
    staged)
        staged=/tmp/staged
        rm -rf "$staged"
        cp -a /usr/local/src "$staged"
        cd "$staged"
        # The merged-/usr skeleton builder/build-package.sh lays into every DESTDIR. The
        # base image has the same links, so an extension carries none of them.
        for link in bin sbin lib lib64; do
            [ ! -e "$link" ] && [ ! -L "$link" ] && continue
            [ -L "$link" ] || { echo "error: /$link in extensions/$name is not the merged-/usr link" >&2; exit 1; }
            rm "$link"
        done
        # Link-time files, as the image's own trim removes them (build-rootfs.sh): an
        # extension's libraries are linked against in staging/ (DEPENDS=, build.sh), and
        # there is no compiler in the guest. The unversioned lib*.so names are symlinks
        # only; at run time the loader asks for the SONAME.
        find . \( -name '*.a' -o -name '*.la' \) -delete
        rm -rf usr/include usr/lib/pkgconfig usr/share/pkgconfig usr/share/aclocal usr/lib/cmake
        find usr/lib -maxdepth 1 -name 'lib*.so' -type l -delete 2>/dev/null || true
        rm -rf usr/share/man usr/share/doc usr/share/info

        # Stripped like the image's own ELF objects (build-rootfs.sh has why it is one
        # file at a time). Go binaries arrive with -s -w already; C ones do not.
        while IFS= read -r -d '' f; do
            LC_ALL=C read -r -N 4 magic < "$f" 2>/dev/null || continue
            [ "$magic" = $'\177ELF' ] || continue
            strip --strip-unneeded "$f" || { echo "error: strip failed on $f" >&2; exit 1; }
        done < <(find . -type f -print0)

        # File capabilities the packages declare (build-rootfs.sh explains the records),
        # applied after the strip, which would drop them, and carried into the image by
        # the tar stream in assemble().
        if [ -d usr/share/kaetzli/capabilities ]; then
            while read -r path caps; do
                [ -n "$path" ] || continue
                [ -f "$path" ] || { echo "error: a capability record names $path, which is not a file" >&2; exit 1; }
                setcap "$caps" "$path"
                getcap "$path"
            done < <(cat usr/share/kaetzli/capabilities/*)
            rm -rf usr/share/kaetzli/capabilities
        fi

        # The extension's SBOM, from its packages' component records the way
        # build-rootfs.sh writes the image's (image/sbom.py): at
        # usr/share/kaetzli/sbom-<name>.json, so a merged extension sits beside the image's
        # own, and as output/sbom-ext-<name>.json, which CI's sbom artifact carries.
        if [ -d usr/share/kaetzli/components ]; then
            write_sbom "$staged" "/usr/local/output/sbom-ext-$name.json"
            cp "/usr/local/output/sbom-ext-$name.json" "usr/share/kaetzli/sbom-$name.json"
            rm -rf usr/share/kaetzli/components usr/share/kaetzli/modules
        fi

        mkdir -p /tmp/staged-sysext /tmp/staged-confext
        rm -rf /tmp/staged-sysext/* /tmp/staged-confext/*
        for top in usr opt; do [ ! -e "$top" ] || mv "$top" /tmp/staged-sysext/; done
        [ ! -e etc ] || mv etc /tmp/staged-confext/
        leftover=$(ls -A)
        [ -z "$leftover" ] || { echo "error: extensions/$name has $leftover, which neither class carries" >&2; exit 1; }
        [ -z "$(ls -A /tmp/staged-sysext)" ]  || assemble sysext  /tmp/staged-sysext
        [ -z "$(ls -A /tmp/staged-confext)" ] || assemble confext /tmp/staged-confext
        cd /
        rm -rf "$staged" /tmp/staged-sysext /tmp/staged-confext ;;
    *) echo "$usage" >&2; exit 2 ;;
esac
