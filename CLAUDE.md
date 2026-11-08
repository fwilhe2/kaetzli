# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

A **pragmatic, automated Linux From Scratch**: source tarballs in, a bootable
`output/rootfs.erofs` and a container image out. `README.md` is the user documentation;
this file is what is easy to get wrong.

**README style:** concise and practical, short paragraphs, clear headings, commands where
useful. Only what users need to act: no repeated background, implementation detail or long
rationale. Prefer updating a section over adding one; deeper detail goes in the relevant guide.

## Aims

A **minimal but genuinely useful general-purpose Linux**, built from source, automatically,
kept current. Small because nothing unnecessary was added, not because something useful was
removed.

- **No self-hosted toolchain.** The Debian builder image is the toolchain, deliberately and
  permanently, not a stepping stone. glibc is the one exception (version skew there silently
  makes binaries that cannot start); everything else links against Debian's.
- **Modern and established, both**: choices a competent engineer would defend in 2026 —
  neither decade-old inertia nor last month's GitHub project.
- **Useful, not merely bootable.** A Kubernetes node is *an* aim, not *the* aim; features are
  judged on whether they serve a general-purpose Linux.

## Project constraints

Fixed decisions. Never propose or implement a change that violates one, even for a requested
simplification: say so and offer an alternative that keeps it.

1. **The stack is systemd + glibc + GNU coreutils + bash.** Never musl, busybox, toybox,
   dinit/OpenRC/runit/s6 or other substitutes; never drop a component to make something build
   or boot. If I ask for a simplification that implies dropping part of this, push back.
2. **VMs are the only target** (qemu now, rust-vmm/Firecracker-style next; real hardware is a
   non-goal). Trim kernel config, drivers and firmware to what a VM needs; no hardware support
   "just in case".
3. **It must run containers.** Namespaces, cgroup v2, overlayfs, netfilter/nftables, veth and
   their userspace must not be configured away.
4. **Networking works end to end** — interfaces, networkd/resolved addressing, DNS, outbound
   — and is *usable* in the guest: `ip`, `ping`, `curl` with TLS and a trust store.
5. **No new interpreters in the image.** bash is the budget. Perl, python, lua or a JS runtime
   in `rootfs/` needs an argument that it is unavoidable; "we could write this in python" never
   is one. Builder and host are unconstrained (perl is in `builder/deps.txt`; perl/python
   `configure` is fine; tests and tools may be Python or Go). Watch `make install` putting
   interpreted helpers in `DESTDIR` (iproute2's `routel` is python; OpenSSL's
   `c_rehash`/`CA.pl`/`tsget` are perl); each `build.sh` deletes its own. `#!/bin/sh` is fine
   (`/usr/bin/sh` is bash). A wrapper is still deleted when what it wraps is missing (curl's
   `curl-config` describes trimmed headers) — a judgement about the wrapper, not the shebang.
6. **Every package is DFSG-free, and says so**: SPDX `LICENSE=` in `env.sh`, checked by
   `test/check-licenses.py` against `test/dfsg-licenses.txt` (its own workflow). A license only in Debian's
   `non-free` means the package does not belong here.

## Adding a package: answer these in the PR first

- **Maintained, or just old?** A last release in 2011 is a red flag. Check what distributions
  ship today.
- **Established?** Debian, Fedora and Alpine all shipping it counts; a 40-star single-maintainer
  repo does not.
- **A more modern option that is equally established?** "What everyone uses" is sometimes a
  decade stale; "what is newest" is almost always wrong.
- **What does it drag in?** Dependencies and size are image decisions (`test/known-missing-libs.txt`,
  `test/size-budget.txt`).
- **Does it need an interpreter at runtime?** (constraint 5) — the most common disqualifier.

## Layout

The root holds only entry points and directories.

```
build.sh          the only build entry point — ./build.sh <package>
packages/<pkg>/   env.sh + build.sh, and the tree its tarball unpacks into (or src/, ours)
builder/          the one builder image, deps.txt (its entire contents), the sysroot entrypoint
image/            staging → image (disk or OCI): Containerfile, build-rootfs.sh, files/ (the
                  shipped /etc), build-extension.sh (tree → system extension)
test/             everything CI runs, plus known-*.txt and size-budget.txt; qemu-lib.sh is the
                  bash boot harness tools/vm.sh and boot-firecracker/vz.sh source
tools/            local conveniences and maintenance, not a build — except lib.sh, sourced by
                  build.sh, tools/ and test/
docs/             design notes, each saying at the top whether it is implemented; how-it-works.md,
                  debugging.md, linking.md
python/           the kaetzli package behind test/ and tools/ .py scripts (python/README.md)
go/               the launcher, go/cmd/kaetzli (docs/proposals/launcher.md)
examples/         images FROM the published image; user templates, not in build or CI
downloads/ staging/<pkg>/{rootfs,extensions} rootfs/ extensions/ output/   (gitignored)
```

`rootfs/` and `extensions/` are merged from staging by `tools/merge-staging.sh`, never
installed into. `artifacts/` is CI's download scratch. Script paths are repo-relative; scripts
in `test/` and `tools/` `cd` there and `source tools/lib.sh` — use its `all_packages`,
`package_name`, `load_env`/`env_get` (an `env.sh` with `$PKG` set, not leaking), `normalize_arch`,
curl helpers and `mib` before writing another `for e in packages/*/env.sh` or `x86_64|amd64)`.

## Host-side code: bash, Python or Go

Constraint 5 is about the image; host, CI and builder code choose per job, and bash is not the
default. `docs/proposals/host-scripting.md` orders the migration (CVE, SBOM, size, library and
policy checks are done).

- **Bash** for command sequences and exit codes: anything a package build sources
  (`builder/build-package.sh`, `packages/*/build.sh`, `env.sh`), thin podman/qemu/git
  wrappers, scripts inside the assembly image. `tools/boot-vz.sh` and what it sources: bash 3.2
  and BSD tools (macOS).
- **Python** for the rest — parsing JSON or tool output, HTTP/GitHub, versions, joins, reports,
  CLIs with subcommands and state (`vm.sh`, `k8s-demo.py`). The default for new host code.
- **Go** only where a static binary is the point (no Python there). The launcher: stdlib only,
  built by `tools/build-launcher.sh` in the golang container `go/go.mod` names; its qemu
  command line is a third copy of the harness's, held equal by `python/tests/test_launcher.py`.
- **`env.sh` stays bash**; Python reads pins through bash (`env_get`, `bash -c 'source …;
  printf …'`), never by parsing.
- **Python 3.12 baseline**: the distribution's `python3` (Debian, Ubuntu, Fedora only). Must run
  on 3.12 and newest (`python.yml` tests both). `requires-python`, ruff `target-version` and
  `python/.python-version` say 3.12, held together by a test; raising it is its own decision.
- **No uv**, nor any tool that installs its own Python — not to run, lock or in CI. `python3 -m
  venv`, pip `--require-hashes`, pip-tools lock. `test_uv_is_not_a_dependency` enforces it.
- **One project, one venv, one lock**: package `kaetzli` in `python/src/` (`kaetzli.repo`,
  `.report`, `.gh`, `.http_cache` are its `lib.sh`; use them first). An entry point is five lines
  calling `python/launch.py`, which keeps `python/.venv` equal to `requirements.txt` (rebuilt
  when lock or interpreter changes). Never `pip install` by hand or rely on host packages; a
  dependency is a `requirements.in` line plus `python/lock.sh` (hashed lock, 3.12, in a
  container). CI: `actions/setup-python` at baseline; Dependabot proposes lock updates two weeks
  after a release.
- **Dependencies** established, maintained, locked and argued like a package (*what does it
  drag in?*): today `requests`, `pyelftools`, `python-debian`, `license-expression`,
  `spdx-tools`, `pexpect`, `pytest`. Prefer stdlib, and the `gh` CLI over a GitHub library.
- **Never in an image.** Assembly-time Python runs in the assembly container on files, imports
  nothing into the tree, is stdlib only (no venv there) and lives beside its caller
  (`image/sbom.py`), not in `kaetzli`; `python/tests/` loads it by path, `python.yml` lints it.
  `test/image-files/interpreters.txt` fails a `.py` in the tree; a python binary needs its own
  `absent` line.
- **Tests first** (pytest in `python/tests/`, seen red then green). `python.yml` also runs
  `ruff check`, `ruff format --check` and a lock recompile. Inject collaborators (`gh` runner,
  HTTP fetch, grype binary) so tests need no network.
- **Lint**: ruff (Python), `go vet`/`gofmt` (Go), `shellcheck` (bash).
- **A rewrite changes the language, not the behaviour**: byte-identical output (reports, SBOMs,
  exit codes) and every `test/selftest.sh` fixture still failing, naming what is wrong. Commit
  the bash version's fixture output as a golden; a bug fixed on the way is edited into the
  golden and said so. One script, or one family sharing code, per PR.

## Commands

`$BOOT` below is `output/rootfs.erofs rootfs/boot/bzImage`.

```sh
./tools/build-image.sh      # everything locally: glibc, kernel, all packages, then erofs + oci
./build.sh glibc            # first — everything compiles against it
./build.sh <pkg>            # one package into staging/<pkg>/, then rootfs/ re-merged
./tools/merge-staging.sh    # rootfs/ + extensions/ from staging/; fails on a file two packages install
./tools/bump-version.sh <pkg> <ver> [--vendor]  # VERSION + SHA256, tarball verified
./tools/check-updates.py [pkg...]   # upstream releases since the pinned VERSION/VENDOR_VERSION/patch series
./tools/check-snapshot.py           # is the pinned Debian snapshot still current?
./tools/bump-snapshot.sh            # move builder + assembler to the newest dated sid snapshot
./tools/check-patches.sh [--version V] [pkg...]  # patches apply to the tarball (seconds, no build)
./tools/sync-patches.sh [--list] <pkg>  # patches/upstream/ = upstream's series (bash53-NNN) or release branch (glibc)
(cd python && python3 launch.py pytest [tests/test_x.py -k name])
(cd python && python3 launch.py ruff check . ../image/*.py && python3 launch.py ruff format --check . ../image/*.py)

# static checks on a tree
./test/check-rootfs-deps.py rootfs        # unresolved NEEDED
./test/check-symbol-versions.py rootfs    # symbol versions no shipped library defines
./test/check-symbol-resolution.py rootfs  # undefined symbols nothing in the NEEDED closure defines
./test/check-hardening.py staging         # RELRO, BIND_NOW, PIE, NX, SSP, fortify per package
./test/kernel-caps.py rootfs              # kernel config vs test/kernel-caps.txt
./test/kernel-parity.py <amd64> <arm64>   # the arches' kernel configs agree
./test/kernel-config-diff.py <old> <new> [src]  # what a kernel build turned on/off
./test/check-licenses.py
./test/check-image-files.sh               # assembled images vs test/image-files/
./test/check-tree-hygiene.sh <tree>       # nothing world-writable, no dangling symlink
./test/check-sbom.py                      # SPDX 2.3, provenance, describes the image
./test/check-doc-links.sh
./test/selftest.sh                        # the tree checks fail on broken fixtures, pass on a good one
./tools/header-skew.py                    # builder -dev headers vs our libraries (report)

# boots (all through test/qemu-lib.sh)
./test/boot.py $BOOT            # smoke, bash as PID 1 (reads rootfs.cmdline beside the image)
./test/systemd.py $BOOT         # no failed units/taint/journal errors; sysctls, fw_cfg; exposure vs test/unit-exposure.txt
./test/network.py $BOOT         # DHCP, DNS, outbound TCP
./test/listeners.py $BOOT       # listening sockets, base and +kubernetes
./test/ssh.py $BOOT             # sshd in-guest and from host (PREBUILT=dir)
./test/container.py $BOOT       # crun
./test/podman.py $BOOT          # seccomp, networking, Quadlet
./test/rootless.py $BOOT        # podman as `user`: ID maps, pasta
./test/persistent-var.py $BOOT  # /var disk survives two boots
./test/sysext.py $BOOT          # /var extensions merge; wrong level refused
./test/debug.py $BOOT           # strace, tcpdump, gdbserver, socat, jq, bpftool, perf
./test/verity.py $BOOT          # tampered image refused
./test/secureboot.py [rootfs]   # signed UKI under OVMF Secure Boot, /var by credentials; unsigned refused (tools/sign-uki.sh first)
./test/vmm.py $BOOT             # Firecracker- and Virtualization.framework-shaped boots
./test/k8s.py $BOOT             # 3-node kubeadm via tools/k8s-demo.py (KVM; skips without)
./test/node-conformance.py $BOOT  # upstream [NodeConformance] node e2e (KVM; FOCUS=, KEEP=1)
./test/oci.sh output/kaetzli-oci.tar  # load and run the container image (no qemu)
./test/prebuilt.sh [build|oci|ssh]    # host-built C, C++, Rust, Go binaries run in the image
./test/reproducible.sh <pkg>...       # build twice from clean worktrees (AGAINST_RUN=<id>: vs CI)
./tools/rebuild-release.py NAME [--no-build]  # a release rebuilt from its commit, vs its SHA256SUMS

# reports
./test/rootfs-size.py [erofs|oci]     # vs test/size-budget.txt (a budget we chose)
./test/vs-debian-slim.py              # OCI vs debian-slim (no number to raise)
./test/size-history.py [arch]         # last dozen main builds (report only)
./test/boot-time-history.py [arch]    # systemd.py's output/boot-time.txt vs main
./test/image-diff.py [OLD [NEW]]      # vs main's newest green build (--comment on the PR)
./test/oci-trim.py [output]           # what OCI leaves out of the disk image, deps check on its layer
./test/cve-scan.py [dir]              # grype on the SBOMs (report only)
./test/cve-workbook.sh [dir]          # reports as <dir>/cve.ods via grind ($GRIND or PATH)
./test/cve-history.py [arch]          # CVE count over main builds, one database
./test/cve-context.py [dir]           # findings + EPSS, KEV, distro verdicts (RUN=, ARCH=)

# running and maintenance
./tools/boot-qemu.py                  # interactive (Ctrl-a x); boot-firecracker.sh (KVM, ssh over vsock), boot-vz.sh (macOS, vfkit)
./tools/ssh.sh [--root] [cmd...]      # passwordless ssh into any of those
./tools/vm.sh start|ssh|stop|list|run|console|update|snapshot [name]  # named machines keeping /var
./tools/demo.sh [reset]               # fresh `demo` machine: verity, identity, sysext, a container
./tools/k8s-demo.py up|ps|kubectl|ssh|down  # 3-node cluster, this checkout's build if any
./tools/build-extension.sh sysext|confext <name> <dir>  # → output/extensions|confexts/<name>.raw
./tools/sign-uki.sh [--key K --cert C] [UKI]  # → output/secureboot/: signed UKI + OVMF with our key
./tools/build-launcher.sh [OUT] [VERSION]
./tools/build-node-e2e.sh             # e2e_node.test + ginkgo for the pinned Kubernetes
./tools/release.py name|publish [--dry-run]
./tools/corresponding-source.py [REV] # repo at REV + its sources image, verified (GPL source offer)
./tools/clean.sh [-n] [--downloads|--images|--all|--vms]  # vm.sh machines only with --vms
```

Builds are slow (glibc, systemd, kernel: tens of minutes) — run them in the background.

**Checks are tested**: `test/selftest.sh` requires every tree check to fail on a fixture
broken one way, *naming what is wrong*. A new check, or a new case in one, gets a fixture
that must produce a finding (a check that skips what it cannot judge passes vacuously).

**A removal gets an `absent` line in `test/image-files/<topic>.txt`** (meson option off,
configure flag, trim line), since a bump or new default undoes it silently; `present` lines
guard what must never be trimmed. `check-image-files.sh` reads the manifest
`output/rootfs-files-<flavour>.txt` (rootfs/ still has what the trim removes).

**Job summaries are text only** — sparklines in markdown tables, never mermaid (the run page
renders each mermaid block in its own iframe).

**`rootfs/` is derived.** `build.sh` builds into `staging/.building-<pkg>/`, moves it to
`staging/<pkg>/` on success, then runs `merge-staging.sh` (sorted, fails on a shared path).
CI's rootfs job does the same from artifacts. Never edit `rootfs/`; `merge-staging.sh`
refuses one it did not make. `build.sh` skips download if the tarball is in `downloads/` and
extraction if `packages/<pkg>/<PACKAGE>/` exists, so editing only `build.sh` re-runs just the
compile. Extraction is `--strip-components=1` into `$PACKAGE`.

## How a package build works

`packages/<pkg>/env.sh` (version, tarball, checksum, license) + `build.sh` (configure,
compile, install); CI's `build` matrix is generated from `packages/`. Shared machinery:

- `builder/Containerfile` + `deps.txt` → the one builder image (bottom of `deps.txt`: what is
  deliberately absent).
- `tools/prep.sh` → the only network step: pull-or-build the builder and sources images
  (content-hash tags, `tools/image-tags.sh`), unpack tarballs. A builder build is seeded with
  the last one's `.deb`s (`builder-debs:<snapshot>-<arch>`, apt-verified), retried, and pushes
  what it fetched even on failure. `FRESH=1`: build both images even if the registry has them,
  without the seed.
- `build.sh` → prep, extract, mounts, run. **The compile is `--network=none`.**
- `builder/build-package.sh` → entrypoint: merged-`/usr` staging, sysroot flags, helpers, then
  sources the package's `build.sh` in the source tree.

**Adding a library to `deps.txt` to make a build work is almost always wrong** (that is the
`known-missing-libs.txt` backlog): configure it out. Build-time interpreters are fine.

`build.sh` is sourced, with `DESTDIR=$ROOTFS --prefix=/usr` and:

| name | |
| --- | --- |
| `$ROOTFS` | this package's (empty) staging tree |
| `MAKEFLAGS` | `-j$(nproc)` |
| `meson_install [opts]` | setup/compile/install with `--buildtype=release -Dlibdir=lib`; never call `meson setup` directly |
| `drop_installed name-or-path…` | delete a `usr/bin` name or `$ROOTFS` path, **failing** if absent — use it, not `rm -f`, whenever a reviewer needs the file gone (interpreter scripts above all); `rm -f` only for long lists |
| `assert_not_linked lib bin…` | fail if a binary has `lib` in `NEEDED` |
| `cargo_vendored [args]` | `cargo build --release --offline --locked` against `./vendor` |

- **Versions**: `PACKAGE`, `TARBALL`, `URL` derive from `VERSION`; never hardcode a version
  elsewhere. Bump with `tools/bump-version.sh` (VERSION alone fails `sources` as *CHECKSUM
  MISMATCH*).
- **Patches** (`docs/guides/patches.md`): `patches/upstream/*` (upstream's series, or a
  release branch as `git format-patch` — glibc follows `release/<v>/master`; owned by
  `tools/sync-patches.sh`, `-p$UPSTREAM_PATCH_STRIP`), then `patches/*.patch` (ours, `-p1`,
  upstream's commit verbatim for an unreleased fix), applied at extraction with `--fuzz=0`.
  Part of the extraction stamp, cache key, sources image and tag, and SBOM. A bump to the
  release containing our patch fails until it is deleted — intended. Sync only appends (a
  changed or vanished committed patch is exit 3) and verifies new files with `gpgv` against
  `packages/<pkg>/upstream-key.asc`; bash, ncurses, glibc and gcc sync nightly via the update
  workflow and auto-merge.
- **`VENDOR_TARBALL`/`_URL`/`_SHA256`**: a second pinned tarball (netavark's crates), handled
  everywhere the first is. With **`VENDOR_VERSION`** it has its own version (tcpdump's libpcap,
  linux-tools' libtraceevent): checked as `<pkg>/<VENDOR_NAME>` from `VENDOR_UPSTREAM_*`, bumped
  with `--vendor`; otherwise it follows `VERSION`.
- **`LOCAL_SOURCE=1`**: source is `packages/<pkg>/src/` (`packages/fetch`), no
  `TARBALL`/`URL`/`SHA256`, mounted **read-only** — compile to `DESTDIR` or `/tmp`. The CI cache
  key hashes `src/`. fetch-sources, prep, image-tags and check-updates skip it.
- **`FOLLOWS=<pkg>`**: build from another package's tarball (`linux-tools` from the kernel).
  `check-updates.py` skips it; `bump-version.sh` refuses it.
- **`packages/gcc`** ships only `libgcc_s.so.1` (glibc dlopens it; no `NEEDED` names it, so the
  deps check cannot see it missing) and `libstdc++.so.6`. Coupled to the builder's gcc: its
  `build.sh` fails when ours lacks a `GCC_*`/`GLIBCXX_*` node Debian's has, so a snapshot that
  moves sid's gcc needs a bump here. It is the only consumer of GMP/MPFR/MPC in `deps.txt`, so
  pin bignum off elsewhere (coreutils `--without-libgmp`, gawk `--disable-mpfr`).
  `test/prebuilt.sh` checks the runtime.
- **Packages that compile nothing** (`iana-etc`) are `install` lines; outside `/usr` is fine
  where glibc hardcodes the path (`/etc/services`).

## The three things that break silently

**1. The wrong glibc.** `build-package.sh` adds `--sysroot` (our glibc) and *appends* the
builder's paths. A build system that *replaces* `CPPFLAGS`/`CFLAGS`/`LDFLAGS` drops the sysroot
(systemd's `-Dc_args` carries it by hand); `check-symbol-versions.py` catches it. `glibc` and
`kernel` are `NO_SYSROOT=1`. The builder also *runs* on our glibc (bind-mounted over every file
Debian's `libc6` owns).

**2. A library only the builder has.** configure finds an optional `-dev` and links it; the
`.so` is missing in qemu. Configure it out (`--without-selinux`). `check-rootfs-deps.py`
reports, `known-missing-libs.txt` allowlists **per library, not per consumer**, so a new
consumer of an allowlisted library passes — iproute2 hides libselinux from `$PKG_CONFIG` and
`assert_not_linked`s `ip`. Check the binary, not the check. Every package compiles against
sid's `-dev` and runs on our library (CI sysroot is glibc alone; curl uses sid's `libssl-dev`,
our `libssl.so.3`), which holds while ours ≥ sid's: `check-symbol-versions.py` (versioned) and
`check-symbol-resolution.py` (unversioned: glib, json-c, pcre2) hold it, `header-skew.py`
shows it, `docs/guides/linking.md` explains it.

**3. No optimization, no hardening.** `build-package.sh` sets `CFLAGS`, so it must export
`-O2` itself (autotools adds it only when unset); `SYSROOT_CFLAGS` is that minus `-O2`, for
meson. Meson defaults to `debug` (`-O0`) silently, hence `meson_install`'s
`--buildtype=release` (systemd's `-Dmode=release` is unrelated). Hardening (Fedora's set:
`_FORTIFY_SOURCE=3`, stack protector, stack clash, CET/BTI, `-z relro -z now`; Go
`-buildmode=pie`) rides in the same variables; glibc gets it from its own configure switches.
A package setting `CFLAGS` wholesale drops sysroot, `-O2` and hardening at once;
`check-hardening.py` catches the last, `test/known-unhardened.txt` lists what is deliberate.

## Image assembly and boot

`image/build-rootfs.sh` turns a copy of `rootfs/` into `output/rootfs.erofs` (or, with `oci`,
`output/kaetzli-oci.tar`): skeleton, `image/files` as `/etc`, trim, first boot, `ldconfig`,
`mkfs.erofs`, verity, SBOM. The assembly image is `image/Containerfile` (snapshot-pinned tools,
content-addressed, `tools/prep.sh assembler`, pushed by the builder job) plus
`image/Containerfile.scripts`, which **`COPY`s `image/files`** — editing it means rebuilding
that layer (`tools/build-image.sh` does). Assembly is byte-reproducible: check a refactor by
building before and after with the same `KAETZLI_BUILD=` and comparing `sha256sum output/*`.

- **Accounts** `root` and `user` (uid 1000), **no passwords**: `shadow` is all `*` and the
  build fails on a hash; the console takes the `login.noauth` credential, ssh keys only.
- **`extra/`** (`build-image.sh --extra DIR`, never in CI) runs after the setuid, shadow and
  trim steps and is held to: add-only, under `usr/`, `etc/`, `opt/`, nothing in
  `$extra_protected`, no setuid or capabilities, recorded in `output/extra-files-<flavour>.txt`
  and the SBOM. Without it the bytes are unchanged.
- **The OCI flavour is subtractions in one block**: kernel; systemd (tree, udev, PAM/NSS
  modules, every binary whose `NEEDED` has `libsystemd-shared`, via `readelf`); PAM; agetty;
  kmod; sshd; libudev; conmon with glib and pcre2. `libsystemd.so.0` **stays** (crun,
  libmount). Deleted libraries are guarded: a new consumer fails the build. **Grep binaries
  with `grep -a`** (GNU grep drops matches inside `.dynstr`). Anything below the block runs
  without systemd, so must say which flavour it is for. `test/oci-trim.py` diffs the manifests
  (side effects included) in the rootfs job summary, so a change to the block is reviewed by
  its result. The OCI archive is hand-written — no buildah or skopeo.
- **The trim** removes only what nothing in the image can reach (not "unlikely to be used"):
  strip (ELF only, failing loudly), link-time files, docs, locales (C only), most gconv
  modules, translated catalogues, most terminfo, completions, unreferenced PAM/NSS modules. Build-time trim:
  systemd's `-D…=false`, the kernel's `vm.config`.
- **The SBOM is generated, not scanned.** `build.sh` passes pins as `KAETZLI_*`,
  `build-package.sh` writes a record into staging, `build-rootfs.sh` turns the *assembled*
  tree's records into SPDX (a package with no file left in a flavour is not in its SBOM, per
  `usr/share/kaetzli/owners`, written by `merge-staging.sh`, deleted by assembly). Written by
  `image/sbom.py` (stdlib, also used by `build-extension.sh` for `sbom-ext-<name>.json`), never
  `printf`. `CPE=` (NVD's names, space-separated) → one `cpe23Type` each; Go modules and Rust
  crates (`go version -m`, `cargo tree -e normal`, in `usr/share/kaetzli/modules/`) → `CONTAINS`
  packages. A patched CVE (`tools/patch-cves.sh`) is a SPDX `fix` reference (commit URL;
  `sbom.py` maps `git+<repo>@<sha>`, the check fails other forms), made OpenVEX by
  `cve-scan.py`. Unresolved libraries are `DEPENDS_ON` without `CONTAINS` (a library = file +
  SONAME symlink).
- **License texts ship for every package; nothing may remove one.** `build-package.sh` copies
  upstream texts (top level, `doc/`, `LICENSES/`/`licenses/`, `debian/copyright`, each
  `vendor/` dependency's) into `usr/share/licenses/<pkg>/`, hardlinking duplicates, failing a
  package with none (`packages/<pkg>/licenses/` stands in: `iana-etc`, `fetch`).
  `build-rootfs.sh` fails if any step changes that tree; `check-sbom.py` requires one per SBOM
  package; a `LicenseRef-` carries its text in `hasExtractedLicensingInfos`.
- **Root is EROFS on dm-verity, read-only.** The hash tree is appended; `output/rootfs.cmdline`
  is **half of the image** (the only place the root hash is): `root=/dev/dm-0` and a
  `dm-mod.create=` table. No initrd (`CONFIG_DM_INIT`), so the kernel cannot check a
  signature on the hash; `output/kaetzli.efi` (UKI: kernel, that command line, our
  systemd's stub; unsigned, reproducible) does, once `tools/sign-uki.sh` signs it and OVMF
  enforces Secure Boot (`docs/guides/secure-boot.md`). Extensions likewise (`<name>.dm-verity`,
  joined by `qemu_sysexts`); `image/files/etc/udev/rules.d/10-dm-init.rules` names dm-init's
  devices, or systemd waits forever.
- **Everything writable is RAM** (`/var` via `var.mount`, `/tmp`); mount points ship empty, so
  anything there is made at boot (tmpfiles, `StateDirectory=`). sshd: privsep
  `/usr/share/empty.sshd`, host keys `/var/lib/sshd` each boot. **`/var` can be a disk**: a
  `systemd.mount-extra=` generates an outranking `var.mount` (`VAR_DISK=` in `qemu-lib.sh`,
  plus a persistent machine ID). **`/home` and `/root` are symlinks to `var/home`,
  `var/roothome`** (`tmpfiles.d/kaetzli-home.conf`); directories in OCI.
- **First boot runs in `build-rootfs.sh`, since `/etc` is read-only**: our `systemd-sysusers`,
  `systemd-tmpfiles --prefix=/etc` and **`systemctl preset-all`** (without it networkd and
  resolved are never enabled), through our loader with `--library-path`. `99-default.preset`
  is `disable *`; a package wanting its unit on ships the `.wants` link (openssh, dbus). **A
  package writing `/etc` at boot breaks the image** (*Read-only file system* in the journal,
  failed by `systemd.py`). Also shipped: empty `/etc/machine-id` (PID 1 binds a per-boot id),
  `/` at 0555 (so tmpfiles does not `fchmod`). The journal catalogue is built at boot.
- **`tools/vm.sh`**: `output/vms/<name>/` holds the `/var` disk, machine ID and a kept ssh key
  (`SSH_KEEP_KEY=1`). Each start copies a changed image there (a rebuild must not write under a
  running root). qemu is `-daemonize`d with a pidfile; `running` checks the pid's cmdline names
  the disk. Sockets live in `$XDG_RUNTIME_DIR` (108-byte limit). Hooks: `QEMU_REBOOT=1`,
  `QEMU_DISPLAY_ARGS`; tests still exit on guest reboot.

**Boot tests.** `boot.py` runs bash as PID 1; the rest boot systemd. `systemd.py` asserts
`running`, no taint, nothing ≥ `err` in the journal, exposure (`systemd-analyze security`)
within `test/unit-exposure.txt`, and records boot time (unjudged; KVM vs KVM — the boot job opens `/dev/kvm` where the runner
has it). **All boots use
`kaetzli.qemu`** (pexpect): every boot test, `tools/boot-qemu.py`, `tools/k8s-demo.py`.
`test/qemu-lib.sh` is only its machine half in bash (no console driver), for `tools/vm.sh`
and the `boot-*.sh` wrappers (host-scripting.md item 7); a change to its command line is a
change to both, held equal by `python/tests/test_qemu_machine.py`.
**Every console is held to `CONSOLE_BAD`** (warnings, oopses, segfaults, emergency mode);
deliberate ones go in a test's `exempt`. `QEMU_PROFILE=microvm|vz` gives Firecracker's or
vz's devices. **`await` matches only output after its mark** (`kaetzli.qemu`: after the last
match or `mark()`) — suspect the handshake before `shadow` when a login fails.

- **D-Bus** is dbus-daemon (logind crash-loops without it). Sessions need `pam_systemd.so`
  (`-Dpam=enabled`).
- **`pam.d/other` is `pam_deny`**: every PAM service needs its own file (`systemd-user`, `su`,
  `runuser`, `sshd`); **`pam.d/systemd-user` must include `pam_systemd.so`** or
  `user@.service` fails on unset `XDG_RUNTIME_DIR`.
- **`80-systemd-osc-context.sh` is masked** (tmpfiles link to `/dev/null`, staged copy removed):
  its OSC 3008 sequences break console parsing. Unmasking is its own commit.
- **`nsswitch.conf`** names only shipped modules. `hosts: resolve [!UNAVAIL=return] files
  myhostname dns`: resolved's NOTFOUND is final; resolved not running falls through.
- **Networking**: networkd (`20-wired.network`, `en*`/`eth*`) + resolved. `network.py` has
  **two rounds, kept apart**: builtins only, then `ip`, `ping`, `curl` over TLS. `ping` targets
  `127.0.0.1` (qemu user net forwards ICMP only if the host allows); non-root ping is a sysctl
  (`50-ping-group-range.conf`), not a capability.
- **TLS**: OpenSSL 3.5 LTS, curl `--with-openssl`, Debian's `ca-certificates` as one bundle at
  `/etc/ssl/certs/ca-certificates.crt` (no hashed `CApath`; `c_rehash` is perl). **OpenSSL is
  coupled to sid's** (curl, openssh compile against sid's `libssl-dev`): stay on 3.x;
  `check-updates.py` holds it to 3.5.
- **sshd** ships units, account, PAM file and config from its own `build.sh` — packages install
  their scaffolding into `DESTDIR`, never `image/files`. `make install-nokeys`, never `install`;
  `--without-openssl-header-check`. **The login key is a per-boot credential**
  (`qemu_ssh_setup`), never read from a home: root's `Match` reads `/run/credentials`, others
  `/run/ssh-authorized-keys/%u`, a copy readable by that user (tmpfiles' copy in a home would
  stay the first boot's forever on a `/var` disk).
- **crun** is the runtime, with seccomp (`packages/libseccomp`, checked on the binary — without
  it profiles are silently ignored).
  **Never `--disable-bpf`** (cgroup v2 device control is BPF).
- **systemd's BPF sandboxing** (`IPAddressAllow=`, `RestrictFileSystems=`) needs
  `packages/libbpf` and `elfutils`; without them it is silently off.
- **iptables is iptables-nft**: legacy binary deleted, plain names → `xtables-nft-multi`, only
  iptables and ip6tables ship. libnftnl: sid's headers, our library.
  `sysctl.d/50-ip-forward.conf` because dockerd's nftables backend does not enable forwarding.

### The container engine and extensions

**podman is the engine.** **Engines are system extensions, the runtime is the base**
(`docs/guides/sysext.md`): podman, netavark, aardvark-dns, passt, shadow are
`EXTENSION=podman`; CRI-O and kubelet `kubernetes`. The base keeps the kernel features, crun,
conmon (glib, pcre2), nftables, libseccomp and `/etc/containers`, so `crun run` works alone.
Attach with `SYSEXTS=podman ./tools/boot-qemu.py`. Units from an extension appear when the
merge reloads PID 1: one that starts at boot needs `UpheldBy=` as well as `WantedBy=`.

- netavark 2.x needs `nft -j` (no iptables driver), so nftables `--with-json` (jansson). glib
  ships only `libglib-2.0`; `libffi-dev` is in `deps.txt` for its build only.
- **Rootless**: `newuidmap`/`newgidmap` (`packages/shadow`), `pasta`, `rootlessport`,
  `/etc/subuid`/`subgid` for `user`. **The helpers are file capabilities, never setuid**,
  declared in `usr/share/kaetzli/capabilities/<pkg>` (tars carry no xattrs), applied at
  assembly; images are made with `--xattrs-include=security.capability`.
- **glib is coupled to sid, unversioned**: keep it ≥ sid's (`check-symbol-resolution.py`
  notices). Stable series is the even minor.
- **Go and Rust ignore C flags**: the sysroot goes via `CGO_*` and one `-C link-arg=` per flag
  in `RUSTFLAGS`; both pinned offline and path-independent.
- **netavark needs kernel symbols Docker's rules do not** (`NFT_FIB_INET`,
  `NF_CONNTRACK_MARK`); a missing one is a bare *"nft" did not return successfully* — feed
  `nft` its rules one at a time.

**The debug extension** (`EXTENSION=debug`; `docs/guides/debugging.md`):

- **An extension's packages cannot link against each other**: a single-consumer library is
  that consumer's `VENDOR_TARBALL`, built static under `/tmp`, with `assert_not_linked`.
- What a tool attaches to must already be in the kernel (`trace.config`).
- kbuild's tools Makefiles reset `CFLAGS`: perf and bpftool get the sysroot via
  `EXTRA_CFLAGS`/`EXTRA_LDFLAGS`. bpftool has no skeletons (they would use the host's BTF).

### The kernel

`packages/kernel/build.sh` = `defconfig` + `kvm_guest.config` + heredoc fragments (only
`build.sh` is mounted): `container`, `k8s`, `guest`, `rootfs`, `kspp`, `trace`, `vm` (takes
defconfig's hardware out), and `parity`, last. A fragment name must not be one upstream uses
(`hardening.config` is, per arch too). **`CONFIG_MODULES` is off**: tristates resolve to `y`/`n`,
and on arm64 an explicit `=m` becomes `y` — hence `vm.config`'s arm64 board list.

- **Unapplicable fragment lines are dropped silently**, so after the merges `build.sh` checks:
  additive fragments' `=y` are `=y`, nothing cleared came back, every named symbol exists in
  some Kconfig. Clearing a symbol means clearing what *selects* it; a clear only takes if
  nothing selects it *at that merge* (a selector a later fragment turns off leaves it on) —
  why `parity.config` is last.
- **Both arches have the same features** (`test/kernel-parity.py`): `parity.config` settles
  defconfig disagreements; hardware goes in `vm.config`, arch internals in
  `test/kernel-parity-allowed.txt`; any new difference fails the job. Try arm64 on amd64 by sourcing `build.sh` with `make` and
  `uname -m` shimmed.
- **Each `iptables -m`/`-j` an engine uses is its own kernel symbol**; a missing one fails at
  runtime (*revision 0 not supported*). No legacy `IP_NF_*` (iptables-nft via `NFT_COMPAT`).
  `kernel-caps.py` holds the list.
- **`SERIAL_8250` never in `vm.config`** (amd64's and Firecracker's console).
- `DEBUG_INFO_BTF` needs the "Debug information" choice settled first; arm64 `BPF_LSM` needs
  `FTRACE` (`BPF_EVENTS`); `SECURITYFS` lets systemd see bpf-lsm.
- No `VT` (needs `EXPERT`), no `tinyconfig`: subtract from defconfig so upstream keeps arch
  requirements current.
- **The kernel follows latest stable from x.y.1.** New `default y` drivers are reported, not
  gated (`kernel-config-diff.py` posted on the PR); clear them or label `hold` between the 17:00
  UTC update run and the 20:00 UTC merge window.

## Reproducible builds

**Same commit, same bytes, any machine.** `KAETZLI_SOURCE_DATE_EPOCH` (`tools/lib.sh`) is every
"now". CI's `reproducible` job assembles twice and requires equal bytes. When touching assembly:

- Every tar goes through `repro_tar` (pax otherwise records atime, ctime, pids).
- Only `security.capability` xattrs (no host SELinux labels).
- Two packages installing one file: fix the package, never the order.
- **Nothing is setuid**: util-linux `--disable-makeinstall-setuid`; assembly fails on
  setuid/setgid, and via `check-tree-hygiene.sh` (let in by `.containerignore`) on
  world-writable paths and dangling symlinks. CI unpacks with `tar xpf`.
- `rootfs/` mtimes do not matter (clamped).

## CI

`.github/workflows/ci.yml`: `changes` → `builder`/`sources` → `glibc` (and `kernel`) → `build`
matrix → `rootfs` → `boot`, `reproducible` → `publish-oci`, `release`; `k8s` and
`node-conformance` gate nothing. Every push runs it; `changes` skips the build only when all
changed files are `docs/**` or root `*.md`; `docs` always runs. **`result` is the one required
check** (fails on any failed/cancelled job or a wanted build that did not boot); a new gating
job goes in its `needs:`.

- **Package jobs** (`.github/actions/build-package`) use a glibc-only sysroot, so each artifact
  holds only its files. Cache key: the build scripts, glibc's `env.sh` and `build.sh`, the
  package's `env.sh`, `build.sh`, `src/` (and its `FOLLOWS` `env.sh`), the builder tag, and the
  half-month — **nothing is cached longer than ~two weeks**. `build.sh`, `tools/lib.sh` and
  `builder/build-package.sh` are in every key: even a comment rebuilds everything. On the 1st and
  15th at 16:00 UTC (or `gh workflow run ci.yml -f fresh=true`) main rebuilds from scratch:
  `FRESH=1` images, no cache. A plain cache miss still pulls both images.
- **`tools/auto-merge.py`** merges green update PRs from 20:00 UTC as they are (no rebase, no
  rebuild, so bumps are never tested together); a red main does not release. `hold` excludes a PR.
- **Only `publish-oci` and `release` write anything public, only on `main`.** A PR cannot
  exercise `publish-oci`: verify it and `tools/publish-oci.sh` by hand (`REGISTRY=` → local
  registry). It pushes `kaetzli:<commit>-<arch>`, then manifest lists `kaetzli:<commit>` and
  `:latest`, after `boot`; both archives load as `localhost/kaetzli:latest`, so it tags and
  pushes each before loading the next. It attests (`actions/attest`) every pushed digest
  (`DIGESTS=`), the OCI SBOMs, and every file in each `bundle.txt`.
- **`release`** runs elsewhere as `tools/release.py publish --dry-run` (all reads, no writes) and is in `result`'s
  `needs`, so a dropped or renamed asset fails before `main`. A new release artifact is a change
  to `collect` in `kaetzli.release`. Images get the snapshot name (`IMAGE_VERSION`) on `main`
  only, in rootfs and reproducible alike.
- **`tools/fetch-image.sh`** gets the latest snapshot release with curl alone (`--snapshot`:
  older; `--run`: a CI run's artifacts) and **installs nothing unverified**: the rootfs job's
  `bundle-<arch>` (run, commit, arch, SHA-256 of every rootfs, kernel and extensions file) must
  match exactly, as must a snapshot's `SHA256SUMS` (and attestations, where `gh` can check). A
  change to those artifacts or assets is a change to that step too. `tools/boot-qemu.py` boots what it fetched.
