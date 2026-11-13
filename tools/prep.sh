#!/usr/bin/env bash
# The network half of a build, on its own:
#
#     ./tools/prep.sh            # make sure both images exist locally, building if needed
#     ./tools/prep.sh --push     # ...and push them to the registry
#     FRESH=1 ./tools/prep.sh    # build both even if the registry has them
#     ./tools/prep.sh assembler  # the image-assembly tools instead (tools/build-image.sh)
#
# After this succeeds a build needs nothing from the internet, which is why build.sh runs
# the compile with --network=none. Run it before going offline, or let build.sh call it.
#
# Both images are content-addressed (tools/image-tags.sh), so "make sure it exists" is
# pull-then-build-on-miss: a tag that is already in the registry is never rebuilt.
set -euo pipefail

cd "$(dirname "$0")/.."
source tools/lib.sh

push=
[ "${1:-}" = "--push" ] && { push=1; shift; }
# CI splits the two: the builder is built once per architecture on a native runner, the
# sources image once for both. Locally the default does whichever is missing.
want="${1:-both}"
case "$want" in
    both|builder|sources|assembler) ;;
    *) echo "usage: $0 [--push] [both|builder|sources|assembler]" >&2; exit 2 ;;
esac

# FRESH=1 is CI's scheduled clean rebuild: a tag that resolves proves the image was built
# once, not that its inputs still build — the snapshot still serving the pinned packages,
# every upstream URL still answering with the pinned bytes. So neither lookup counts, both
# images are built from their inputs, and --push replaces what the tag pointed at. The
# inputs are pinned, so it is the same image by everything the tag claims to describe.
have() { [ -z "${FRESH:-}" ] && podman image exists "$1"; }
pull() { [ -z "${FRESH:-}" ] && podman pull --quiet "$1" >/dev/null 2>&1; }

# A manifest list and a plain image do not take the same push verb, and which one is
# under a given name is not a thing to assume: the sources image is *built* as a list,
# but `podman pull` of a list gives you only the entry matching this host, so the same
# tag is a list here and an image there. Ask instead.
# push_image <image> [destination] — the destination defaults to the image's own name.
push_image() {
    if podman manifest inspect "$1" >/dev/null 2>&1; then
        podman manifest push --all "$1" ${2:+"$2"}
    else
        podman push "$1" ${2:+"$2"}
    fi
}

# The assembly tools are on their own: the builder job pushes them beside the builder, and
# only image assembly (tools/build-image.sh, tools/build-extension.sh) asks for them, so
# a package build never needs them and they never need a sources image.
if [ "$want" = assembler ]; then
    assembler=$(./tools/image-tags.sh assembler)
    if have "$assembler"; then
        echo ">> assembler: $assembler (already local)" >&2
    elif pull "$assembler"; then
        echo ">> assembler: $assembler (pulled)" >&2
    else
        echo ">> assembler: $assembler (building)" >&2
        podman build -t "$assembler" -f image/Containerfile . >&2
        [ -z "$push" ] || push_image "$assembler" >&2
    fi
    echo "$assembler"
    exit 0
fi

builder=$(./tools/image-tags.sh builder)
sources=$(./tools/image-tags.sh sources)

# The builder's packages, kept between builds (builder/Containerfile has how apt uses
# them): one image per snapshot and arch, since the snapshot pins every package's bytes.
# Nothing in it is trusted — apt takes a file only when its SHA-256 matches the signed
# index — so it may come from any earlier build. FRESH=1 builds without it: proving the
# snapshot still serves every package is that build's point.
debs_dir=output/builder-debs
debs_image="${builder%/builder:*}/builder-debs:$(sed -n 's/^ARG SNAPSHOT=//p' builder/Containerfile)-$(normalize_arch)"

seed_debs() {
    mkdir -p "$debs_dir"
    [ -z "${FRESH:-}" ] || return 0
    if ! podman pull --quiet "$debs_image" >/dev/null 2>&1; then
        echo ">> builder: no $debs_image to seed from"
        return 0
    fi
    local ctr
    ctr=$(podman create "$debs_image" /nonexistent)
    podman cp "$ctr:/debs/." "$debs_dir/"
    podman rm "$ctr" >/dev/null
    echo ">> builder: $(debs_count) packages seeded from $debs_image"
}

debs_count() { find "$debs_dir" -maxdepth 1 -name '*.deb' | wc -l | tr -d ' '; }

# push_debs: the directory as $debs_image, when it holds more than the seed did. After a
# failed build too: the next one starts from what this one got.
push_debs() {
    [ -n "$push" ] && [ "$(debs_count)" -gt "$seeded" ] || return 0
    local ctx
    # Beside the directory, not in /tmp: hard links do not cross filesystems.
    ctx=$(mktemp -d "$debs_dir.push.XXXXXX")
    mkdir "$ctx/debs"
    find "$debs_dir" -maxdepth 1 -name '*.deb' -exec ln {} "$ctx/debs/" \;
    printf 'FROM scratch\nCOPY debs/ /debs/\n' > "$ctx/Containerfile"
    podman build -q -t "$debs_image" "$ctx" >/dev/null
    rm -rf "$ctx"
    podman push "$debs_image"
    echo ">> builder: pushed $(debs_count) packages as $debs_image"
}

# build_builder: with the seed mounted, a few times over. snapshot.debian.org refuses
# requests under load (503, 504) for minutes at a time, past what apt's own retries wait
# out; each attempt keeps what it fetched, so the next asks only for what is missing.
build_builder() {
    seed_debs
    seeded=$(debs_count)
    local attempt
    for attempt in 1 2 3 4 5; do
        if podman build -t "$builder" -f builder/Containerfile \
                --volume "$PWD/$debs_dir:/debs:z" .; then
            push_debs
            return 0
        fi
        echo ">> builder: attempt $attempt failed, $(debs_count) packages fetched so far" >&2
        [ "$attempt" = 5 ] || sleep $((attempt * 60))
    done
    push_debs
    echo "error: could not build $builder: snapshot.debian.org kept refusing" >&2
    return 1
}

# Whether the registry already had it, which is the same question as whether there is
# anything to push. Content-addressed tags make that exact: a tag that resolved is by
# definition the image we would have built, so pushing it again is at best wasted work.
# It is also actively wrong for the sources image — `podman pull` of a manifest list
# gives you the one image matching this host, not the list, so a later
# `podman manifest push` fails with "image is not a manifest list".
builder_pulled=
sources_pulled=

if [ "$want" = sources ]; then
    : # skip the builder entirely
elif have "$builder"; then
    echo ">> builder: $builder (already local)"
elif pull "$builder"; then
    echo ">> builder: $builder (pulled)"
    builder_pulled=1
else
    echo ">> builder: $builder (building)"
    build_builder
fi

# Having the image is not the same as having the tarballs where a build can see them, so
# unpack it into downloads/ afterwards. This is what makes the registry the first place a
# source is looked for and the internet the fallback, rather than the other way round.
extract_sources() {
    local ctr
    ctr=$(podman create "$sources" /nonexistent)
    mkdir -p downloads
    podman cp "$ctr:/sources/." downloads/
    podman rm "$ctr" >/dev/null
    # Not used from here: the checkout's packages/<pkg>/patches are what a build applies.
    rm -rf downloads/patches
}

# The last sources image pushed, whatever its pins: a bump changes one or two tarballs,
# so the rest are already in it. It is a cache and never trusted: a file in it is used
# only where its SHA256 is the one an env.sh pins, wherever and under whatever name it
# is, and fetch-sources.sh verifies everything again.
sources_latest="${sources%:*}:latest"

# pinned_tarballs — "<tarball> <sha256>" per line, for every pinned source archive.
pinned_tarballs() {
    for pkg in $(all_packages); do
        ( load_env "$pkg"
          [ -n "${LOCAL_SOURCE:-}" ] && exit 0
          echo "$TARBALL $SHA256"
          [ -z "${VENDOR_TARBALL:-}" ] || echo "$VENDOR_TARBALL $VENDOR_SHA256" )
    done
}

# seed_downloads — before fetching, put into downloads/ every pinned tarball the last
# sources image has, so only the new ones come from upstream. Not with FRESH=1, whose
# point is that every upstream still serves the pinned bytes.
seed_downloads() {
    [ -z "${FRESH:-}" ] || return 0
    if ! podman pull --quiet "$sources_latest" >/dev/null 2>&1; then
        echo ">> sources: no $sources_latest to seed from, fetching everything"
        return 0
    fi
    local seed ctr sha path tarball n=0
    seed=$(mktemp -d)
    ctr=$(podman create "$sources_latest" /nonexistent)
    podman cp "$ctr:/sources/." "$seed/"
    podman rm "$ctr" >/dev/null
    rm -rf "$seed/patches"
    # Go modules by directory: fetch-sources.sh keeps a file only where go.sum's hash
    # matches, and fetches the rest.
    if [ -d "$seed/go-modules" ]; then
        mkdir -p downloads/go-modules
        cp -rn "$seed/go-modules/." downloads/go-modules/
        rm -rf "$seed/go-modules"
    fi
    declare -A by_sha=()
    while read -r sha path; do by_sha[$sha]=$path; done \
        < <(find "$seed" -type f -print0 | xargs -0 -r sha256sum)
    mkdir -p downloads
    while read -r tarball sha; do
        [ -n "${by_sha[$sha]:-}" ] || continue
        cp "${by_sha[$sha]}" "downloads/$tarball"
        n=$((n + 1))
    done < <(pinned_tarballs)
    rm -rf "$seed"
    echo ">> sources: $n of $(pinned_tarballs | wc -l) tarballs from $sources_latest"
}

if [ "$want" = builder ]; then
    :
elif have "$sources"; then
    echo ">> sources: $sources (already local)"
    extract_sources
elif pull "$sources"; then
    echo ">> sources: $sources (pulled)"
    sources_pulled=1
    extract_sources
else
    echo ">> sources: $sources (building)"
    seed_downloads
    ./tools/fetch-sources.sh
    # Only the pinned tarballs go in. downloads/ is cumulative and keeps whatever an
    # earlier checkout fetched — three kernel releases, say — and none of that belongs
    # in an image whose tag claims to describe exactly one set of versions.
    stage=$(mktemp -d)
    trap 'rm -rf "$stage"' EXIT
    mkdir -p "$stage/sources"
    for pkg in $(all_packages); do
        # LOCAL_SOURCE packages have no tarball to vendor — their source is in git, which
        # is a better copy than this image could be.
        ( load_env "$pkg"
          if [ -z "${LOCAL_SOURCE:-}" ]; then
              cp "downloads/$TARBALL" "$stage/sources/$TARBALL"
              if [ -n "${VENDOR_TARBALL:-}" ]; then
                  cp "downloads/$VENDOR_TARBALL" "$stage/sources/$VENDOR_TARBALL"
              fi
              # GO_MODULES=1: its modules, as fetch-sources.sh left them (exactly go.sum's).
              if [ -n "${GO_MODULES:-}" ]; then
                  mkdir -p "$stage/sources/go-modules"
                  cp -r "downloads/go-modules/$PACKAGE" "$stage/sources/go-modules/$PACKAGE"
              fi
              # The patches too, as /sources/patches/<pkg>/, so the image alone is every
              # byte of source a build compiles. A build reads them from the checkout, which
              # has the same files; this copy is for whoever has only the image.
              if [ -d "packages/$pkg/patches" ]; then
                  mkdir -p "$stage/sources/patches"
                  cp -r "packages/$pkg/patches" "$stage/sources/patches/$pkg"
              fi
          fi )
    done
    printf 'FROM scratch\nCOPY sources /sources\n' > "$stage/Containerfile"
    # A manifest list covering both architectures, from one build. There is nothing to
    # emulate — the image has no RUN, only a COPY of the same bytes — so this costs
    # nothing and saves storing 450 MB of tarballs once per architecture.
    #
    # Two traps here, both of which produce a list with an entry for this host only and
    # no complaint — and the failure lands on whichever architecture is missing, at pull
    # time, in a different job:
    #
    #   - --manifest creates the list itself, so do not `podman manifest create` first.
    #     An existing list is added to rather than replaced, so a stale one from an
    #     interrupted run survives with only its old entry refreshed.
    #   - a cached build short-circuits to the host's image and never produces the other
    #     platform. --no-cache is what stops that, and it costs nothing worth having:
    #     this image is one COPY of files already on disk.
    podman manifest rm "$sources" >/dev/null 2>&1 || true
    podman build --no-cache --manifest "$sources" --platform linux/amd64,linux/arm64 \
        -f "$stage/Containerfile" "$stage"
fi

if [ -n "$push" ]; then
    if [ "$want" != sources ] && [ -z "$builder_pulled" ]; then
        push_image "$builder"
    fi
    if [ "$want" != builder ] && [ -z "$sources_pulled" ]; then
        push_image "$sources"
        push_image "$sources" "$sources_latest"
    fi
fi

[ "$want" = sources ] || echo "$builder"
[ "$want" = builder ] || echo "$sources"
