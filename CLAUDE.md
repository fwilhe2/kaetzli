# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

A **pragmatic, automated Linux From Scratch**: source tarballs in, a bootable
`output/rootfs.erofs` and a container image out. `README.md` is the user-facing
documentation; this file covers what is easy to get wrong when changing things.

## Documentation style

Keep the README concise and practical. Match its existing tone: short paragraphs, clear
headings, and commands where useful. Add only what users need to act; avoid repeated
background, implementation detail, and lengthy rationale. Prefer updating an existing
section over adding one. Keep deeper technical explanations in the relevant guide.

## What this is, and what it is not

The goal is a **minimal but genuinely useful general-purpose Linux stack**, built from
source, automatically, and kept current. Minimal is a means here, not the point: the
image should be small because nothing unnecessary was added, not because something useful
was removed.

**Pragmatic** is the load-bearing word, and it separates this from LFS proper:

- **No self-hosted toolchain.** Real LFS bootstraps gcc and binutils through several
  passes to escape the host. That is a step too far for what this is for. The Debian
  builder image is the toolchain, deliberately and permanently — it is a solid, current,
  well-maintained choice, and *not* a stepping stone to something else. The one place
  the host is escaped is glibc, because a version skew there silently produces binaries
  that cannot start; everything else links against Debian's and that is fine.
- **Modern and established, both.** Every technology choice should be one a competent
  engineer would defend in 2026 — neither the crusty option that a decade of inertia
  chose, nor whatever appeared on GitHub last month. This is not an exercise in
  recreating Debian oldstable, and it is not a place to chase novelty.
- **Useful, not merely bootable.** An eventual target is running as a node in a
  Kubernetes cluster, but that is *an* aim, not *the* aim — the image should be a
  reasonable general-purpose Linux, and features are judged on whether they serve that.

## Project constraints

These are fixed decisions, not preferences. Do not propose or implement changes that
violate them, even when a request asks for simplification — if a simplification would
require breaking one, say so and offer an alternative that keeps it.

1. **The stack is systemd + glibc + GNU coreutils + bash.** Never swap in musl, busybox,
   toybox, dinit/OpenRC/runit/s6, or any other substitute, and never drop a component to
   make something build or boot. If I ask for a simplification that implies dropping part
   of this stack, treat it as out of scope and push back.
2. **Virtual machines are the only target.** qemu today, rust-vmm/Firecracker-style VMMs
   next; real hardware is explicitly a non-goal. Kernel config, drivers and firmware
   should be trimmed to what a VM needs — do not add hardware support "just in case".
3. **It must be able to run containers.** Kernel features (namespaces, cgroup v2, overlayfs,
   netfilter/nftables, veth) and the userspace to go with them are in scope and must not be
   configured away.
4. **Networking has to work end to end.** A booted image needs working interfaces,
   addressing (systemd-networkd/systemd-resolved), DNS and outbound connectivity — not just
   a kernel that has the drivers. And it has to be *usable* from inside the guest, which
   is a separate claim and was false for a long time: `ip`, `ping` and `curl` (with TLS
   and a trust store behind it) are what make the difference between a machine that has a
   network and one somebody can debug.
5. **No new interpreters in the image.** `bash` is the one it has, and that is the budget.
   A package that would put perl, python, lua or a JavaScript runtime into `rootfs/`
   needs an argument that it is unavoidable, not merely convenient — and "we could write
   this bit in python" is never that argument. What the *builder* and the *host* need is
   unconstrained: perl is already in `builder/deps.txt`, a package whose `configure` is
   perl or python is fine, and the repository's own tests and tools may be Python or Go
   (see *Host-side code* below). The rule is about what ships. Watch `make install`, which is where an
   interpreted helper script sneaks into `DESTDIR` — iproute2's `routel` is python and
   OpenSSL's `c_rehash`/`CA.pl`/`tsget` are perl. Each package's `build.sh` deletes its
   own. `#!/bin/sh` is *not* one of these cases: `packages/bash/build.sh` links
   `/usr/bin/sh` at bash, so a shell script runs here. A wrapper is still deleted when
   what it wraps is missing — curl's `curl-config` describes headers the trim removes —
   but that is a judgement about the wrapper, not about the shebang.
6. **Every package is DFSG-free, and says so.** `LICENSE=` in `env.sh`, as an SPDX
   expression, checked by `test/check-licenses.py` against `test/dfsg-licenses.txt` in its
   own workflow. Debian's guidelines are the bar because Debian has already argued every
   one of these to a conclusion. A license that only exists in `non-free` means the
   package does not belong here, however good it is.

## Adding a package: the questions to ask first

Packaging is the easy part. Choosing is where the mistakes are, so before writing an
`env.sh`, answer these — in the pull request, where they can be disagreed with:

- **Is it still maintained, or is it just old?** A last release in 2011 is a red flag, not
  a sign of stability. Check what the distributions actually ship today.
- **Is it established?** Something Debian, Fedora and Alpine all ship has been through
  more scrutiny than this project can apply. A GitHub project with forty stars and one
  contributor has not, whatever its README claims.
- **Is there a more modern option that is equally established?** Both halves matter. The
  default answer to "what does everyone use" is often right and sometimes a decade stale;
  the answer to "what is newest" is almost always wrong.
- **What does it drag in?** A dependency is a decision about the image, not an
  implementation detail — see `test/known-missing-libs.txt` for what happens when that
  goes unasked. Size is a real cost too, and `test/size-budget.txt` will say so.
- **Does it need an interpreter at runtime?** See constraint 5. This is the one that most
  often disqualifies an otherwise reasonable choice.

## Layout

Everything in the root is either an entry point or a directory; nothing else belongs
there.

```
build.sh          the only build entry point — ./build.sh <package>
packages/<pkg>/   env.sh + build.sh per package, and the tree its tarball unpacks into
                  (or src/, for the few whose source is ours rather than upstream's)
builder/          how a package is compiled: the one builder image, deps.txt (its
                  entire contents), and the entrypoint that sets up the sysroot
image/            how the staging tree becomes an image, disk or OCI: Containerfile,
                  build-rootfs.sh, and files/ — the /etc the image ships; and
                  build-extension.sh, how a tree becomes a system extension for it
test/             everything CI runs to verify a build, plus known-missing-libs.txt,
                  known-journal-messages.txt and size-budget.txt; qemu-lib.sh is the
                  boot harness the qemu tests and tools/boot-qemu.sh all source
tools/            local conveniences and maintenance, not part of a build — except
                  lib.sh, the helpers build.sh, tools/ and test/ all source
docs/             design notes; each says at the top whether it is implemented.
                  how-it-works.md is the mechanics and pitfalls of the system as it is,
                  debugging.md the debug extension, linking.md how a binary finds its
                  libraries
python/           the kaetzli package behind the .py scripts in test/ and tools/: its
                  code, tests, requirements.in and the one lock (python/README.md)
examples/         images built FROM the published kaetzli image, each with its own README;
                  templates for users, not part of a build or of CI
downloads/        source tarballs (gitignored)
staging/          each package's own install tree, staging/<pkg>/{rootfs,extensions} (gitignored)
rootfs/           merged from every staging tree by tools/merge-staging.sh, never installed into (gitignored)
extensions/       the same merge per system extension, for packages with EXTENSION= (gitignored)
output/           built images, fetched CI artifacts, test console logs (gitignored)
```

Paths in scripts are relative to the repository root, and the ones under `test/` and
`tools/` `cd` there themselves, so they work from any directory. Having done that, they
`source tools/lib.sh` for what they share — `all_packages`, `package_name`, `load_env` /
`env_get` (a package's `env.sh`, with `$PKG` set and without leaking into the caller),
`normalize_arch`, the curl invocations and `mib`. Reach for it before writing another
`for e in packages/*/env.sh` or another `x86_64|amd64)` case. `artifacts/` is CI's
scratch directory for downloaded artifacts — it deliberately does not collide with
`packages/` or `image/`.

## Host-side code: bash, Python or Go

Constraint 5 is about the image. What runs on the host, in CI or in a builder container
is not bound to bash, and bash is not the default: it is hard to read and review, and much
of `test/` and `tools/` is JSON, HTTP, joins and report tables written in jq, awk and
`declare -A`. Choose per job. `docs/proposals/host-scripting.md` reviews every script
against this and orders the migration; the CVE reports, the SBOM, the size reports,
the library checks and the policy checks are done.

- **Bash** where the job *is* a sequence of commands and their exit codes: anything
  sourced by a package build (`builder/build-package.sh`, `packages/*/build.sh`, `env.sh`,
  whose vocabulary is shell functions), thin wrappers around podman/qemu/git, and scripts
  that run inside the assembly image. `tools/boot-vz.sh` and what it sources stay within
  bash 3.2 for macOS.
- **Python** for the rest: anything that parses JSON or tool output, talks to an HTTP API
  or GitHub, compares versions, joins data sets, renders a report, or is a CLI with
  subcommands and state (`vm.sh`, `k8s-demo.sh`). The default for new host-side code
  that is not a command sequence.
- **Go** only where a static binary is the point: something that must run where there
  is no Python. Not as a second general-purpose language beside Python.
- **`env.sh` stays bash.** Python reads a package's pins through bash (`env_get`, or
  `bash -c 'source …; printf …'`), never by parsing the file.
- **Python 3.12 is the baseline**: the host's `python3`, from the distribution (Debian,
  Ubuntu, Fedora; nothing else is a target for the tooling). Code must run on 3.12 and on
  the newest release, and `python.yml` tests both. `requires-python`, ruff's
  `target-version` and `python/.python-version` say 3.12, and a test holds them together;
  raising it is a decision of its own, made when the oldest distribution we care about
  has moved.
- **uv is a no-go dependency.** It is a fine tool, but it is one company's, and the
  tooling must not depend on it: not to run, not to lock, not in CI. The same goes for
  any tool that installs its own Python. The standard pieces do the job: `python3 -m
  venv`, pip with `--require-hashes`, and a lock compiled by pip-tools.
  `test_uv_is_not_a_dependency` fails on a reference to it.
- **One project, one venv, one lock.** The code is the `kaetzli` package in
  `python/src/` (`kaetzli.repo`, `.report`, `.gh`, `.http_cache` are what `tools/lib.sh`
  is to bash: reach for them first). An entry point in `test/` or `tools/` is five lines
  that call `python/launch.py`, which keeps `python/.venv` equal to
  `python/requirements.txt` (made again from scratch when the lock or the interpreter
  changes) and execs the module there, from any directory. Never `pip install` by hand,
  never rely on the host's packages; a dependency is a line in `requirements.in` and a
  `python/lock.sh`, which compiles the hashed lock on 3.12 in a container. CI uses
  `actions/setup-python` with the baseline, and Dependabot (`pip`) proposes lock updates
  two weeks after a release.
- **Dependencies** must be established and maintained (`requests`, `pyelftools`,
  `python-debian`, `license-expression`, `spdx-tools`, `pexpect`, `pytest`), pinned by the
  lock, and argued like a package: *what does it drag in?* Prefer the stdlib when it is
  enough, and the `gh` CLI over a GitHub client library (it already holds the auth).
- **Never in an image.** A Python helper used during assembly runs in the assembly
  container (python3 in `image/Containerfile` is builder-side), on files, and nothing it
  imports is copied into the tree. It is stdlib only, since that container has no venv,
  and so it lives beside its caller in `image/` (`image/sbom.py`), not in `kaetzli`;
  `python/tests/` loads it by path and `python.yml` lints it with the rest.
  `test/image-files/interpreters.txt` already fails a `.py` in the tree; a python binary
  would need an `absent` line of its own.
- **Tests, first.** Python gets pytest tests under `python/tests/`, written before the
  code and seen to fail (red, then green), and `.github/workflows/python.yml` runs them
  with `ruff check`, `ruff format --check` and a recompile of the lock. Collaborators are
  injected (`gh`'s runner, the HTTP fetch, grype's binary) so a test needs no network.
- **Lint**: `ruff check` and `ruff format` for Python, `go vet`/`gofmt` for Go,
  `shellcheck` for bash.
- **A rewrite changes the language, not the behaviour**, and is proved like a refactor
  of `build-rootfs.sh`: the same output before and after (byte-identical reports,
  SBOMs, exit codes), and every check `test/selftest.sh` covers still fails on its
  fixtures, naming what is wrong. The bash version's output on the fixtures is committed
  as a golden file the Python must reproduce; a bug fixed on the way is a deliberate
  difference, edited into the golden and said so. One script, or one family sharing
  code, per pull request.

## Commands

```sh
./build.sh glibc            # must come first — everything else compiles against it
./build.sh <package>        # one package into staging/<package>/, then rootfs/ re-merged from all of them
./tools/merge-staging.sh    # rootfs/ and extensions/ from staging/, failing on a file two packages install
./test/check-rootfs-deps.py rootfs      # unresolved NEEDED entries
./test/check-symbol-versions.py rootfs  # symbol versions no shipped library defines
./test/check-symbol-resolution.py rootfs  # undefined symbols nothing in a binary's NEEDED closure defines
./tools/header-skew.py            # builder -dev headers vs the libraries we ship (report)
./test/kernel-caps.py rootfs            # kernel config vs test/kernel-caps.txt: what a container needs
./test/kernel-parity.py <amd64> <arm64>  # the two arches' kernel configs agree (configs or trees)
./test/kernel-config-diff.py <old> <new> [src]  # what a kernel build turned on or off
./test/check-licenses.py                # every package declares a DFSG-free license
./test/selftest.sh                      # the static checks above fail on broken fixture trees, and pass on a good one
./tools/check-updates.py [pkg...] # what upstream has released since the pinned VERSION, VENDOR_VERSION and patch series
./tools/check-patches.sh [--version V] [pkg...]  # packages/<pkg>/patches apply to the tarball (seconds; no build)
./tools/sync-patches.sh [--list] <pkg>  # patches/upstream/ = upstream's patch series (bash53-NNN) or release branch (glibc)
./test/boot.py output/rootfs.erofs rootfs/boot/bzImage   # headless boot smoke test (reads rootfs.cmdline beside the image)
./test/systemd.py output/rootfs.erofs rootfs/boot/bzImage  # no failed units, taint, journal errors; sysctls, fw_cfg
./test/network.py output/rootfs.erofs rootfs/boot/bzImage  # DHCP + DNS + outbound TCP
./test/listeners.sh output/rootfs.erofs rootfs/boot/bzImage  # listening sockets vs the expected list, base and +kubernetes
./test/ssh.sh output/rootfs.erofs rootfs/boot/bzImage        # sshd, in-guest and from the host (PREBUILT=dir: and those binaries)
./test/container.sh output/rootfs.erofs rootfs/boot/bzImage  # crun starts a container
./test/podman.sh output/rootfs.erofs rootfs/boot/bzImage     # podman: seccomp, networking, Quadlet
./test/rootless.sh output/rootfs.erofs rootfs/boot/bzImage   # podman as `user`: ID maps, pasta
./test/persistent-var.sh output/rootfs.erofs rootfs/boot/bzImage  # two boots, one /var disk: state survives
./test/sysext.sh output/rootfs.erofs rootfs/boot/bzImage   # extensions on the /var disk are merged at boot, a wrong level refused
./test/debug.sh output/rootfs.erofs rootfs/boot/bzImage    # the debug extension: strace, tcpdump, gdbserver, socat, jq, bpftool, perf
./test/verity.sh output/rootfs.erofs rootfs/boot/bzImage   # a tampered copy of the image is refused
./test/vmm.sh output/rootfs.erofs rootfs/boot/bzImage      # Firecracker- and Virtualization.framework-shaped boots
./test/k8s.sh output/rootfs.erofs rootfs/boot/bzImage      # 3-node kubeadm cluster via tools/k8s-demo.sh (KVM; skips without)
./test/node-conformance.sh output/rootfs.erofs rootfs/boot/bzImage  # upstream's [NodeConformance] node e2e specs on one node (KVM; FOCUS=, KEEP=1)
./test/reproducible.sh <pkg>...   # build twice from clean worktrees, compare to the byte (AGAINST_RUN=<id>: against CI's)
./test/oci.sh output/kaetzli-oci.tar # load and run the container image (no qemu)
./test/prebuilt.sh [build|oci|ssh] # host-compiled C, C++, Rust and Go binaries, run in the image
./test/rootfs-size.py [erofs|oci]  # image size vs test/size-budget.txt, and where it went
./test/vs-debian-slim.py          # the OCI image against debian-slim, with both breakdowns
./test/size-history.py [amd64|arm64]  # both flavours against the last dozen builds on main
./test/check-sbom.py              # the SBOMs validate as SPDX 2.3 (spdx-tools), carry their provenance and describe the image
./test/cve-scan.py [dir]          # known CVEs in the SBOMs, by grype (report only; the cve-scan job)
./test/cve-workbook.sh [dir]      # cve-scan.py's reports as <dir>/cve.ods, by grind ($GRIND or PATH; CI builds it from main)
./test/cve-history.py [amd64|arm64]  # the CVE count over the last builds on main, rescanned with one database, steps by update/other
./test/cve-context.py [dir]       # cve-scan.py's findings (output/, else CI's newest; RUN=, ARCH=) with EPSS, CISA KEV, Debian/Ubuntu/Red Hat verdicts, grouped by triage hint (report)
./test/check-doc-links.sh         # relative links in the tracked markdown resolve
./test/check-image-files.sh       # each assembled image against test/image-files/: what must and must not ship
./test/check-tree-hygiene.sh <tree>  # nothing world-writable, no dangling symlink (build-rootfs.sh runs it on each flavour)
./test/oci-trim.py [output]       # what the OCI image leaves out of the disk image, and a deps check on its layer
./tools/vm.sh start|ssh|stop|list|run|console|update|snapshot [name] ...  # named machines that keep /var
./tools/demo.sh [reset]           # a fresh `demo` vm.sh machine: verity root, identity, sysext attach, a container
./tools/boot-qemu.sh              # interactive boot (Ctrl-a x to exit)
./tools/boot-firecracker.sh       # the same, in Firecracker (Linux + KVM; ssh over vsock)
./tools/boot-vz.sh                # the same, in Virtualization.framework via vfkit (macOS)
./tools/ssh.sh [--root] [cmd...]  # passwordless ssh into the guest any of those started, as `user` or root
./tools/k8s-demo.sh up|ps|kubectl|ssh|down  # a 3-node kubeadm cluster on the image and its kubernetes extension, this checkout's build if there is one (docs/guides/sysext.md)
./tools/clean.sh [-n] [--downloads|--images|--all|--vms]  # delete build state, tarballs, stale images; vm.sh's machines only with --vms
./tools/build-extension.sh sysext|confext <name> <dir>  # a directory tree → output/extensions|confexts/<name>.raw
./tools/build-node-e2e.sh         # e2e_node.test + ginkgo for the pinned Kubernetes, in a golang container
```

The checks above plus a real boot are the verification story for the image. The Python
tooling has its own unit tests (`python3 python/launch.py pytest`, *Host-side code*). The checks themselves are tested: `test/selftest.sh` builds small
trees broken in one way each and requires every tree check to fail on its fixture *naming
what is wrong*. A check that skips what it cannot judge passes vacuously, so a new check,
or a new case in one, gets a fixture that must produce a finding.

Builds are slow (glibc, systemd and the kernel are tens of minutes); run them in the
background. Image assembly is byte-reproducible, so a refactor of `image/build-rootfs.sh`
can be checked by building before and after with the same `KAETZLI_BUILD=` and comparing
`sha256sum` of everything in `output/`.

**A removal gets an `absent` line in `test/image-files/<topic>.txt`** — a meson option
off, a configure flag, a trim line — because each is undone in silence by a version bump
or a new default. `check-image-files.sh` holds the manifest `build-rootfs.sh` writes of
each assembled tree (`output/rootfs-files-<flavour>.txt`; rootfs/ still has everything
the trim removes) to those lines, and `present` lines guard what must never be trimmed.

`rootfs-size.py` checks against a budget we chose; `vs-debian-slim.py` against the image
somebody would otherwise use, and has no number to raise. `size-history.py` only reports.
**Any job summary has to be text — sparklines in a markdown table, no mermaid**: the run
page concatenates every job's summary and renders each mermaid block in its own iframe.

**Each package installs into a tree of its own, and `rootfs/` is derived.** `build.sh`
builds into `staging/.building-<pkg>/`, moves it to `staging/<pkg>/` only on success, and
runs `tools/merge-staging.sh`, which rebuilds `rootfs/` and `extensions/` from every staging
tree in sorted order and fails on any path two packages both install. CI's rootfs job
unpacks the package artifacts into `staging/` and calls the same script. Never edit
`rootfs/` by hand; `merge-staging.sh` refuses a `rootfs/` it did not make.

`build.sh` skips the download when the tarball is in `downloads/` and the extract when
`packages/<pkg>/<PACKAGE>/` exists, so editing only a `build.sh` re-runs just the compile.
The extract is `--strip-components=1` into a directory named after `$PACKAGE`, so the tree
is versioned even when upstream's is not (`ca-certificates`).

## How a package build works

A package is two files, `packages/<pkg>/env.sh` (version, tarball, checksum, license) and
`packages/<pkg>/build.sh` (configure/compile/install). The CI `build` matrix is generated
from `packages/`. Everything else is shared:

- `builder/Containerfile` + `builder/deps.txt` → the one builder image. `deps.txt` is its
  entire contents; its bottom lists what is deliberately absent and why.
- `tools/prep.sh` → the only network step: pull-or-build the builder and sources images
  (content-hash tags, `tools/image-tags.sh`), unpack the tarballs. `FRESH=1` builds both
  even when the registry has them.
- `build.sh` (root) → the driver: prep, extract, mounts, run. **The compile is
  `--network=none`.**
- `builder/build-package.sh` → the container entrypoint: merged-`/usr` staging, sysroot
  flags, helpers, then `source /package-build.sh` in the source tree.

**Adding a library to `builder/deps.txt` to make a build work is almost always wrong** —
that is how `test/known-missing-libs.txt` got its backlog. Configure the dependency out.
That is about libraries; a build-time interpreter is fine (perl is in `deps.txt`). What
must not happen is an interpreted script landing in `DESTDIR` (constraint 5).

`packages/<pkg>/build.sh` is bind-mounted and *sourced*, so it gets this vocabulary.
Install with `DESTDIR=$ROOTFS` and `--prefix=/usr`.

| name | what it is for |
| --- | --- |
| `$ROOTFS` | this package's staging tree, empty at the start — the `DESTDIR` |
| `MAKEFLAGS` | `-j$(nproc)`, so a plain `make` is parallel |
| `meson_install [opts]` | setup/compile/install, `--buildtype=release -Dlibdir=lib`; never call `meson setup` directly |
| `drop_installed name-or-path…` | delete a `usr/bin` name or a path under `$ROOTFS`, and **fail** if it was not installed |
| `assert_not_linked lib bin…` | fail if an installed binary has `lib` in `NEEDED` |
| `cargo_vendored [args]` | `cargo build --release --offline --locked` against `./vendor` |

`drop_installed` rather than `rm -f` whenever a reviewer would want to be sure the file is
gone — an interpreter script above all — because an `rm -f` of a renamed file deletes
nothing and says nothing. `rm -f` is for lists longer than any one build installs.

**Versions.** `PACKAGE`, `TARBALL` and `URL` derive from `VERSION`; never hardcode a version
elsewhere. `SHA256` cannot be derived, so a bump is two lines: use
`tools/bump-version.sh <pkg> <version>`, which writes both and verifies the tarball. A bump
of `VERSION` alone fails the `sources` job as *CHECKSUM MISMATCH*.

**Patches** (`docs/guides/patches.md`): `packages/<pkg>/patches/upstream/*` (a series
upstream publishes, or a release branch as `git format-patch` — glibc follows
`release/<v>/master` — owned by `tools/sync-patches.sh`, `-p$UPSTREAM_PATCH_STRIP`) then
`patches/*.patch` (ours, `-p1`), applied by `build.sh` at extraction in the builder with
`--fuzz=0`. They are in the extraction stamp, the cache key, the sources image and its tag,
and the SBOM. Ours is upstream's commit verbatim, for a fix not yet released; a bump to the
release that has it fails until the patch is deleted, which is the point. A sync only
appends (a changed or vanished committed patch is exit 3) and, with
`packages/<pkg>/upstream-key.asc`, verifies every new file with `gpgv` against that pinned
key; bash, ncurses, glibc and gcc follow series this way, nightly, through the update
workflow and auto-merge.

**`VENDOR_VERSION`**: a second tarball with a version of its own (tcpdump's libpcap,
linux-tools' libtraceevent) is checked by `check-updates.py` as `<pkg>/<VENDOR_NAME>`, from
`VENDOR_UPSTREAM_*`, and moved by `bump-version.sh --vendor`. Without it the vendor tarball
follows `VERSION` (netavark's crates).

**`LOCAL_SOURCE=1`**: the source is `packages/<pkg>/src/` in git (`packages/kaetzlifetch`), with
no `TARBALL`, `URL` or `SHA256`. `build.sh` mounts it **read-only**, so the build must
compile straight to `DESTDIR` or under `/tmp`. The CI cache key hashes `src/` because the
source can change with `env.sh` untouched. fetch, prep, image-tags and check-updates all
skip it.

**`packages/gcc`** builds gcc and ships only `libgcc_s.so.1` (glibc dlopens it — no `NEEDED`
names it, so the deps check cannot see it missing) and `libstdc++.so.6`. Its version is
coupled to the builder's gcc: its `build.sh` fails when ours lacks a `GCC_*`/`GLIBCXX_*`
version node Debian's defines, so a snapshot bump that moves sid's gcc means a bump here.
It is the one consumer of GMP/MPFR/MPC in `deps.txt`, which other configures can then find
— pin bignum support off (coreutils `--without-libgmp`, gawk `--disable-mpfr`).
`test/prebuilt.sh` checks the runtime with binaries the host's gcc, g++, rustc and go built.

**Packages that compile nothing** (`packages/iana-etc`) are two `install` lines. Installing
outside `/usr` is fine where glibc hardcodes the path (`/etc/services`).

## The three things that break silently

**1. Compiling against the wrong glibc.** `build-package.sh` points gcc at our glibc with
`--sysroot` and appends the builder's own paths so other dependencies resolve to Debian's.
A build system that *replaces* rather than appends to `CPPFLAGS`/`CFLAGS`/`LDFLAGS` drops
the sysroot (systemd's `-Dc_args` carries it by hand). `test/check-symbol-versions.py`
catches this. `glibc` and `kernel` are `NO_SYSROOT=1`. The builder container also *runs* on
our glibc: `build.sh` bind-mounts ours over every file Debian's `libc6` owns.

**2. Linking against a library only the builder has.** configure finds an optional `-dev`
package, links it, and the missing `.so` shows up in qemu. Configure it out
(`--without-selinux`). `test/check-rootfs-deps.py` reports them; `known-missing-libs.txt`
allowlists the accepted backlog. **The allowlist is per library, not per consumer**, so a
new package picking up an allowlisted library passes: iproute2 would have linked
libselinux (allowlisted for nscd) with no flag to stop it, so it hides the library from
`$PKG_CONFIG` and `assert_not_linked`s `ip`. Check the binary, not the check.

The same arrangement is how every package links every library we ship: the CI sysroot is
glibc alone, so curl compiles against sid's `libssl-dev` and runs on our `libssl.so.3`.
It holds while our version is at least sid's — held by `check-symbol-versions.py` for
versioned libraries and `check-symbol-resolution.py` for unversioned ones (glib, json-c,
pcre2); `tools/header-skew.py` shows both sides. `docs/guides/linking.md` is the long version.

**3. No optimization.** Autotools picks `-O2` only when `CFLAGS` is unset, and
`build-package.sh` sets it, so it exports `-O2` itself; `SYSROOT_CFLAGS` is the same without
`-O2`, for meson. Meson's default buildtype is `debug` (`-O0`) and says nothing:
`meson_install` passes `--buildtype=release`. systemd's `-Dmode=release` is unrelated (log
and status format). A package that sets `CFLAGS` wholesale drops both sysroot and `-O2`.

## Image assembly and boot

`image/build-rootfs.sh` turns `rootfs/` into `output/rootfs.erofs` (or, with `oci`,
`output/kaetzli-oci.tar`) on a copy in the container: skeleton, `image/files` as the shipped
`/etc`, the trim, first boot, `ldconfig`, `mkfs.erofs`, verity, the SBOM. **`image/files` is
`COPY`ed into the assembly image**, so editing it means rebuilding that image
(`tools/build-image.sh` does). Accounts are `root` and `user` (uid 1000) with **no
passwords**: `shadow` is all `*` and the build fails on a hash; the console takes the
`login.noauth` credential, ssh takes keys only.

**`extra/` is opt-in** (`build-image.sh --extra DIR`, never in CI) and runs after the
setuid, shadow and trim steps, so `build-rootfs.sh` holds it to its own rules: add-only,
under `usr/`, `etc/`, `opt/`, nothing in `$extra_protected`, no setuid or capabilities,
and recorded in `output/extra-files-<flavour>.txt` and the SBOM. Without it, the bytes are
the same as before it existed.

**The container flavour is subtractions from the disk image, in one block**: the kernel,
systemd (its tree, udev, PAM/NSS modules, and every binary whose `NEEDED` names
`libsystemd-shared`, found by `readelf`), PAM (broken without `/etc/pam.d` anyway), agetty,
kmod (no modules), sshd, libudev, conmon with glib and pcre2. `libsystemd.so.0` **stays**
(crun, libmount). Libraries deleted this way are guarded: a new consumer fails the build.
**Grepping binaries for a library name needs `grep -a`** — GNU grep otherwise discards a
match inside an ELF `.dynstr`. Anything added *below* the subtractions block runs on a tree
without systemd, so it has to say which flavour it is for. `test/oci-trim.py` shows the
whole difference from the two manifests, side effects included, and runs the deps check
on the OCI layer; CI puts it in the rootfs job's summary, so a change to the block is
reviewed by its result. The OCI archive is written by
hand; do not add buildah or skopeo. `test/oci.sh` loads and runs it, on the host's kernel.

**The trim** removes what nothing in the image can reach — that is the bar, not "unlikely
to be used": strip (ELF only, failing loudly), link-time files, docs, locales (C locale
only), most gconv modules, translated catalogues, most terminfo, completions, PAM and NSS
modules no config names. The rest of the trim is at build time: systemd's `-D…=false`
lines and the kernel's `vm.config`.

**The SBOM is generated, not scanned** (there is no package database): `build.sh` passes
each package's pins as `KAETZLI_*`, `build-package.sh` writes a record into the staging tree
after a successful install, and `build-rootfs.sh` turns the records in the *assembled* tree
into SPDX, since the flavours differ: a package with no file left in a flavour is not in
its SBOM, judged by `usr/share/kaetzli/owners`, which `merge-staging.sh` writes into `rootfs/`
and the assembly deletes. `image/sbom.py` writes it, stdlib Python on the assembly
image's python3, never `printf`; `test/check-sbom.py` checks it. A package's `CPE=` (NVD's
name for it, space-separated where NVD has two) becomes a `cpe23Type` reference each, and the Go modules and Rust crates a program
compiled in (`go version -m`, `cargo tree -e normal`, recorded by `build-package.sh` in
`usr/share/kaetzli/modules/`) become packages it `CONTAINS`: those two are what scanners match.
A CVE a patch fixes (`tools/patch-cves.sh`) is a SPDX `fix` reference on its package (the
commit's URL: `image/sbom.py` maps `git+<repo>@<sha>`, and the check fails a form it does not), and
`test/cve-scan.py` makes OpenVEX of those, so grype reports it patched rather than open.
`image/sbom.py` is shared with `build-extension.sh`, which writes `sbom-ext-<name>.json`.
Unresolved libraries
are `DEPENDS_ON` without `CONTAINS`, computed like `check-rootfs-deps.py` (a library is a
file plus its SONAME symlink).

**License texts ship with every package and nothing may remove one.** After the install,
`build-package.sh` copies each package's upstream texts (top level, `doc/`, any `LICENSES/`
or `licenses/` directory, `debian/copyright`, and every `vendor/` dependency's) into
`usr/share/licenses/<pkg>/`, hardlinking duplicates, and fails a package that has none;
`packages/<pkg>/licenses/` stands in for a source with no text (`iana-etc`, `kaetzlifetch`).
`build-rootfs.sh` hashes that tree before the first step and fails if any step changed it,
and `check-sbom.py` requires one for every package the SBOM names. A `LicenseRef-` carries
those texts in the SBOM's `hasExtractedLicensingInfos`, without which it means nothing.

**The root is EROFS on dm-verity, attached read-only.** The hash tree is appended to the
image, and `output/rootfs.cmdline` beside it — **half of the image**, the only place the
root hash is — carries `root=/dev/dm-0` and a `dm-mod.create=` table. No initrd: the kernel
builds the device (`CONFIG_DM_INIT`). `test/verity.sh` checks a tampered copy is refused.
Extensions are verified the same way (`<name>.dm-verity`, joined to the root's table by
`qemu_sysexts`); `image/files/etc/udev/rules.d/10-dm-init.rules` names dm-init's devices,
without which systemd waits on them forever.

**Everything writable is RAM** — `/var` (a `var.mount` unit), `/tmp` — and the image ships
those mount points empty, so anything needed there is made at boot (tmpfiles,
`StateDirectory=`). sshd's privsep directory is `/usr/share/empty.sshd` and its host keys
`/var/lib/sshd`, made every boot. **`/var` can be a disk, chosen by the VM**: a
`systemd.mount-extra=` on the command line generates a `var.mount` that outranks the
image's (`VAR_DISK=` in `test/qemu-lib.sh`, which also passes a persistent machine ID).
**`/home` and `/root` are symlinks to `var/home` and `var/roothome`**, as on ostree
systems, so they are wherever `/var` is, with no mount of their own to order;
`tmpfiles.d/kaetzli-home.conf` makes the targets. The OCI flavour keeps them as directories.

**First boot happens in `build-rootfs.sh`, because `/etc` is read-only.** The units that
write `/etc` are `ConditionNeedsUpdate=/etc` and skip, which is right only if the result is
there: so the build runs our `systemd-sysusers`, `systemd-tmpfiles --prefix=/etc` and
**`systemctl preset-all`** — without the last, networkd and resolved are never enabled and
the machine boots `running` with no network. `99-default.preset` is `disable *`; a package
that wants its unit running ships the `.wants` link itself (openssh, dbus). These tools are
ours, run through our loader with `--library-path` (their RUNPATH would resolve inside the
container). **A package that writes `/etc` at boot breaks the image**, seen only as
*Read-only file system* in the journal, which `test/systemd.py` fails on. The build also
ships an empty `/etc/machine-id` for PID 1 to bind-mount a per-boot id over, and `/` at
0555 so tmpfiles does not try to `fchmod` it. The journal catalogue is compiled at boot by
`systemd-journal-catalog-update` (`/var` starts empty), not here.

**`tools/vm.sh` machines are state, images are copies.** `output/vms/<name>/` holds the
`/var` disk, machine ID and an ssh key made once (`SSH_KEEP_KEY=1` to `qemu_ssh_setup`).
Each start copies the image there when its source changed, because qemu reads the root
for the whole boot and a rebuild must not write under it. qemu is `-daemonize`d with a
pidfile, so no shell owns it; `running` checks that the pid's command line names the
machine's disk. Its sockets are in `$XDG_RUNTIME_DIR`, because a socket path is limited
to 108 bytes. `QEMU_REBOOT=1` (no `-no-reboot`) and `QEMU_DISPLAY_ARGS` are the
`qemu_argv` hooks it uses; every test still exits on a guest reboot.

`test/boot.py` runs `/bin/bash` as PID 1; the others boot systemd. `test/systemd.py` asserts
`running` (no failed unit), no taint and nothing at `err` or above in the journal.
**All boots go through the one harness**, `test/qemu-lib.sh`, which `tools/boot-qemu.sh`
sources too, so debugging a CI failure boots the same machine. Its Python twin,
`kaetzli.qemu` (pexpect), is what `test/boot.py`, `systemd.py` and `network.py` use, and the rest move to it one by one
(`docs/proposals/host-scripting.md`, item 7); until then a change to one is a change to
both, and `python/tests/test_qemu_machine.py` runs the bash harness to hold the command
lines, patterns and login strings equal. **Every boot's console is held to
`CONSOLE_BAD`** (kernel warnings, oopses, segfaults, emergency mode) when the test ends
without having failed; a test that provokes one on purpose names it in
`CONSOLE_BAD_EXEMPT`. `QEMU_PROFILE=microvm|vz` gives Firecracker's or
Virtualization.framework's devices (`test/vmm.sh`). `tools/boot-vz.sh` runs on macOS:
what it uses from `qemu-lib.sh` and `lib.sh` stays within bash 3.2 and BSD tools.
**`await` only matches output after the mark it is given** (in `kaetzli.qemu` every wait
sees only what follows the last match or `mark()`) — matching the whole
transcript once typed the password into the username prompt, so suspect the handshake
before `shadow` when a login fails.

- **D-Bus** is dbus-daemon; logind crash-loops without it. Sessions need `pam_systemd.so`
  (systemd `-Dpam=enabled`).
- **`pam.d/other` is `pam_deny`**: every PAM service needs its own file (`systemd-user`,
  `su`, `runuser`, `sshd`). **`pam.d/systemd-user` must include `pam_systemd.so`**, or
  `user@.service` fails on an unset `XDG_RUNTIME_DIR`.
- **`/etc/profile.d`**: systemd's `80-systemd-osc-context.sh` is masked (tmpfiles snippet
  linked to `/dev/null`, and the staged copy removed): it wraps prompts in OSC 3008
  sequences on the console the tests parse. Unmasking it is its own commit.
- **`nsswitch.conf`** may only name modules in the image. `hosts` is `resolve
  [!UNAVAIL=return] files myhostname dns`: resolved's NOTFOUND is final, resolved *not
  running* falls through.
- **Networking** is networkd (`20-wired.network`, `en*`/`eth*`) and resolved.
  `test/network.py` runs **two rounds**: builtins only (does it have a network?), then
  `ip`, `ping`, `curl` over TLS (can someone use it?). Keep them apart. `ping` targets
  `127.0.0.1` because qemu user networking forwards ICMP only if the host allows it; a
  sysctl (`50-ping-group-range.conf`), not a capability, lets non-root ping.
- **TLS** is OpenSSL 3.5 LTS, curl `--with-openssl`, and Debian's `ca-certificates` as one
  bundle at `/etc/ssl/certs/ca-certificates.crt` (no hashed `CApath`: `c_rehash` is perl).
  **OpenSSL is coupled to sid's**: curl and openssh compile against sid's `libssl-dev`, so
  ours must stay on 3.x (`check-updates.py` holds it to 3.5).
- **sshd** ships its units, account, PAM file and config from its own `build.sh` — a
  package installs its scaffolding into `DESTDIR`, never into `image/files`, which ships
  whatever was built. `make install-nokeys`, never `install`. openssh's configure runs a
  version check against the wrong libcrypto, hence `--without-openssl-header-check`.
  **The login key is a per-boot credential** (`qemu_ssh_setup`), and `sshd_config` never
  reads it from a home, where tmpfiles' copy is written only if missing and so, on a
  `/var` disk, would be the first boot's forever: root's `Match` block reads
  `/run/credentials`, everyone else `/run/ssh-authorized-keys/%u`, a copy readable by
  the user sshd reads it as.
- **crun** is the runtime: podman's default, the smallest. Built with seccomp
  (`packages/libseccomp`, checked on the binary: without it profiles are silently ignored).
  **Never `--disable-bpf`**: the cgroup v2 device controller is a BPF program, and every
  bundle has a device rule.
- **systemd's own BPF sandboxing** (`IPAddressAllow=`, `RestrictFileSystems=`, …) loads
  through **libbpf**, hence `packages/libbpf` and `elfutils` (libelf). Without the library
  those settings are silently off, with one warning at boot.
- **iptables** is iptables-nft: the legacy binary is deleted and the plain names point at
  `xtables-nft-multi`; only iptables and ip6tables ship. libnftnl follows the usual rule
  (sid's headers, our library). dockerd's nftables backend does not enable forwarding
  itself, hence `sysctl.d/50-ip-forward.conf`.

### The container engine and extensions

**podman is the engine** (no daemon, crun's ecosystem, Quadlet). **Engines are system
extensions, the runtime is the base** (`docs/guides/sysext.md`): podman, netavark, aardvark-dns,
passt and shadow are `EXTENSION=podman`; CRI-O and kubelet are `kubernetes`. The base keeps
the kernel features, crun, conmon (with glib, pcre2), nftables, libseccomp and
`/etc/containers`, so `crun run` works on the base alone. Attach with
`SYSEXTS=podman ./tools/boot-qemu.sh`. Quadlet units from an extension appear when the merge
reloads PID 1, so one that should start at boot needs `UpheldBy=` as well as `WantedBy=`.

- netavark 2.x has **no iptables driver**: it needs `nft -j`, so nftables `--with-json`
  (jansson). glib ships only `libglib-2.0`; `libffi-dev` is in `deps.txt` for its build only.
- **Rootless** needs `newuidmap`/`newgidmap` (`packages/shadow`), `pasta`, `rootlessport`,
  and `/etc/subuid`/`subgid` for `user`. **The two helpers are file capabilities, never
  setuid**: a package declares them in `usr/share/kaetzli/capabilities/<pkg>` (package tars
  carry no xattrs) and the assembly applies them. Images are made from a tar stream with
  `--xattrs-include=security.capability`, so no host SELinux label gets in.
- **glib is coupled to sid like OpenSSL, and unversioned**: keep `packages/glib` at or
  above sid's; `check-symbol-resolution.py` is what notices. glib's stable series is the
  even minor.
- **Go and Rust read none of the C flags**: `build-package.sh` passes the sysroot through
  `CGO_*` and one `-C link-arg=` per flag in `RUSTFLAGS` (cargo splits on whitespace), and
  pins both offline and path-independent.
- **`VENDOR_TARBALL`/`VENDOR_URL`/`VENDOR_SHA256`**: a second pinned tarball (crates for
  netavark, aardvark-dns), handled everywhere the first one is.
- **netavark's rules need kernel symbols Docker's do not** (`NFT_FIB_INET`,
  `NF_CONNTRACK_MARK`), and a missing one is *"nft" did not return successfully* with
  nothing after it. Find the next one by feeding `nft` netavark's rules one at a time.

**The debug extension** (`EXTENSION=debug`; `docs/guides/debugging.md`, `test/debug.sh`):

- **An extension's packages cannot link against each other.** A library with one consumer
  is that consumer's `VENDOR_TARBALL`, built static under `/tmp`, with `assert_not_linked`.
- **`FOLLOWS=<pkg>`** builds from another package's tarball (`linux-tools` from the kernel's
  `env.sh`). `check-updates.py` skips it; `bump-version.sh` refuses it.
- **What a tool attaches to has to be in the kernel already** (`trace.config`).
- kbuild's tools Makefiles start `CFLAGS` from scratch, so perf and bpftool get the sysroot
  as `EXTRA_CFLAGS`/`EXTRA_LDFLAGS`. bpftool is built without skeletons (they would compile
  against the build host's BTF).

### The kernel

`packages/kernel/build.sh` is `defconfig` + `kvm_guest.config` + fragments written as
heredocs (only `build.sh` is mounted): `container.config`, `k8s.config`, `guest.config`,
`rootfs.config`, `kspp.config`, `trace.config`, `vm.config`, which takes defconfig's
hardware back out, and `parity.config`, merged last. A fragment's name must not be one
upstream uses (`hardening.config` is, per arch too, and `make` merges them all).
**`CONFIG_MODULES` is off**: everything is built in, so with modules off
kconfig resolves every tristate to `y` or `n` — and on arm64 an explicit `=m` becomes `y`,
which is why `vm.config` names so much arm64 board support.

- **A fragment line that cannot be applied is dropped in silence**, so `build.sh` checks
  after `make vm.config`: everything the additive fragments turn on is `=y`, nothing
  cleared came back `=y`, and every named symbol is defined by some Kconfig (a rename
  upstream fails the build). When clearing something, expect to clear what *selects* it
  — and a clear only takes if nothing selects the symbol *at that merge*: a selector a
  later fragment turns off leaves the symbol on, which is why `parity.config` is last.
- **Both arches have the same features** (`test/kernel-parity.py`, the `kernel-parity`
  job): their defconfigs disagree about much that is not the architecture's, and
  `parity.config` decides it once. A new difference fails the job; hardware goes in
  `vm.config`, the arch's own internals in `test/kernel-parity-allowed.txt`.
  `kernel/configs` can be resolved for either arch without a compile by sourcing
  `build.sh` with `make` and `uname -m` shimmed, which is how a change to it is tried
  for arm64 on an amd64 host.
- **Each `iptables -m`/`-j` an engine uses is a kernel symbol of its own**, and a missing one
  fails at runtime (*revision 0 not supported*). No legacy `IP_NF_*`: iptables is
  iptables-nft through `NFT_COMPAT`. `test/kernel-caps.py` holds the kernel to the list, `test/kernel-caps.txt`.
- **`SERIAL_8250` must never be in `vm.config`**: it is amd64's and Firecracker's console.
- `DEBUG_INFO_BTF` needs the "Debug information" choice settled first, and `BPF_LSM` needs
  `FTRACE` (for `BPF_EVENTS`) on arm64; `SECURITYFS` is how systemd sees bpf-lsm.
- Not `VT` (only settable under `EXPERT`), and not `tinyconfig`: subtracting from defconfig
  lets upstream keep the architecture's own requirements current.
- **The kernel follows the latest stable**, from x.y.1. New `default y` drivers are reported,
  not gated: the kernel job posts `test/kernel-config-diff.py` against main on the pull
  request, and the 24 hours before auto-merge are the time to clear them or label `hold`.

## Reproducible builds

**The same commit builds the same bytes, on any machine.** `KAETZLI_SOURCE_DATE_EPOCH` in
`tools/lib.sh` is every "now" (`SOURCE_DATE_EPOCH` in each build). `test/reproducible.sh`
builds a package twice and compares (`AGAINST_RUN=<id>`: against CI's), and CI's
`reproducible` job assembles the images a second time and requires the same bytes. When
touching assembly:

- Every tar goes through `repro_tar` (GNU tar's pax format otherwise records atime, ctime
  and pids).
- Only `security.capability` xattrs, so host SELinux labels stay out.
- Two packages installing one file fail `merge-staging.sh`; fix the package, never the
  order.
- **Nothing is setuid**: util-linux is `--disable-makeinstall-setuid` and the assembly fails
  on any setuid or setgid file, and through `test/check-tree-hygiene.sh` (copied into the
  assembly image, so `.containerignore` lets it in) on a world-writable path or a
  dangling symlink. CI unpacks with `tar xpf`.
- mtimes in `rootfs/` do not matter: assembly clamps them.

## CI

`.github/workflows/ci.yml`: `changes` → `builder`/`sources` → `glibc` (and `kernel`) →
`build` matrix → `rootfs` → `boot`, `reproducible` → `publish-oci`, with `k8s` and
`node-conformance` beside them gating nothing. **Every push runs it**: `changes` skips the
build when every changed file is `docs/**` or a root `*.md` (anything it cannot tell
builds), `docs` (`test/check-doc-links.sh`) always runs, and **`result` is the one check
to require** — it fails on any failed or cancelled job and on a wanted build that did not
boot. A new gating job goes in its `needs:`.
Each package job uses `.github/actions/build-package`, which stages a glibc-only sysroot so
each artifact holds only its own files. Its cache key hashes the build scripts, glibc's
`env.sh` and `build.sh` (every package compiles against them), the package's `env.sh`,
`build.sh` and `src/` (and the `env.sh` it `FOLLOWS`), the builder tag, and the half-month:
**nothing is served from the cache for more than about two weeks**. On the 1st and 15th at
20:00 UTC a scheduled run rebuilds main from scratch — `FRESH=1` rebuilds the builder and
sources images, and the package jobs skip the cache — and `gh workflow run ci.yml -f
fresh=true` does the same by hand. A plain cache miss is not that: it still pulls both
images. Note that `build.sh`, `tools/lib.sh` and `builder/build-package.sh` are in every
key, so even a comment there rebuilds every package.

`publish-oci` is the only job that writes anything public, and only on `main`, so a pull
request cannot exercise it — verify changes to it or `tools/publish-oci.sh` by hand
(`REGISTRY=` points it at a local registry). It pushes `kaetzli:<commit>-<arch>`, then a
manifest list at `kaetzli:<commit>` and `kaetzli:latest`, after `boot` (the OCI image is the same
userspace as the disk). Both archives `podman load` as `localhost/kaetzli:latest`, so the
script tags and pushes each before loading the next.

`tools/fetch-image.sh` / `tools/boot-qemu.sh` are for poking at CI artifacts locally.
**fetch installs nothing it has not verified**: the rootfs job uploads `bundle-<arch>`, a
manifest of run, commit, arch and the SHA-256 of every file in the rootfs, kernel and
extensions artifacts, and fetch requires an exact match before swapping the new bundle in.
A change to what those artifacts contain is a change to that step too.
