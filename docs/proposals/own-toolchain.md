# Our own toolchain

**Status: proposal, nothing implemented.** This would reverse a stated aim: "No
self-hosted toolchain. The Debian builder image is the toolchain, deliberately and
permanently" (`CLAUDE.md`, [what-kaetzli-is.md](../what-kaetzli-is.md)). Adopting any phase
past the first is a decision to change that aim, and both files change with it. None of
the six project constraints is affected.

The goal: take the latest gcc, Go, Rust and libraries when upstream releases them, not
when Debian uploads them to sid. The phases below are independent enough to stop after any
of them.

## What couples us to sid today

| Coupling | Where it shows | Consequence |
| --- | --- | --- |
| C/C++ compiler | `deps.txt` `build-essential`, `g++` | gcc major moves when sid moves; `packages/gcc` must match it (`UPSTREAM_IGNORE`, version-node check) |
| Go, Rust | `golang-go`, `rustc`, `cargo` | podman, kubernetes, netavark build only once sid has the Go/Rust they require |
| Library headers | the `-dev` section of `deps.txt` (~25 lines) | ours must stay ≥ sid's: OpenSSL held to 3.x, glib ≥ sid, `check-symbol-resolution.py`, `header-skew.py` |
| Kernel UAPI headers | `linux-libc-dev` | glibc and systemd see sid's kernel headers, not our kernel's |
| Build tools | autotools, meson, cmake, python, perl, bison, pahole, clang (BPF) … | almost none: they do not reach the image |

Debian cannot be removed entirely: something has to compile the first compiler. The
realistic aim is that **Debian becomes the host, not the toolchain**, as the host system
is in LFS: nothing Debian ships reaches the image as code, headers or ABI.

## Phase 1: Go and Rust from upstream binaries

Replace `golang-go`, `rustc`, `cargo` with upstream's release tarballs (go.dev/dl,
static.rust-lang.org), installed in the builder image.

- **Pinned, not floating.** "Always latest" means a version and SHA-256 per arch in a pin
  file, bumped by `check-updates.py` and the nightly update PRs like any package. A
  floating "latest" would break *same commit, same bytes*. The pin file is hashed into the
  builder tag by `tools/image-tags.sh`.
- **Signatures**: both upstreams publish detached signatures next to their tarballs. Pin
  the keys and verify them like `SIG_URL` and `upstream-key.asc`. `auto-merge.py` must not
  merge a key change.
- Fetch in `tools/prep.sh` (the only network step). `GOTOOLCHAIN=local` stays.
- Rust: a minimal profile (`rustc`, `cargo`, `rust-std`) for the host triple only.
- Both are builder-side, so constraint 5 does not apply.

**Effort**: 1–2 days. **Build time**: none. The builder image grows by roughly 400 MB.
**Ongoing**: Go point releases monthly, Rust every six weeks, both automated.

The gain is immediate: Kubernetes and podman often require a Go minor release before sid
ships it.

## Phase 2: binutils and gcc as the compiler

`packages/gcc` already builds `cc1` and `cc1plus` (16 minutes on amd64, today's longest
package job) and throws them away. Keep them, add binutils, and install both into a
toolchain artifact that `build-package.sh` puts first on `PATH`. Debian's gcc becomes only
the seed compiler that builds ours.

- Configure gcc with `--with-sysroot` set to our glibc. The compiler then cannot find
  another glibc, so silent break 1 ("the wrong glibc") goes away by construction. Most of
  the sysroot plumbing can also go: `-idirafter`, the `-B/-L` list, systemd's `-Dc_args`,
  `CGO_*` and the `RUSTFLAGS` link args.
- Put hardening in the compiler's defaults (`--enable-default-pie`,
  `--enable-default-ssp`, CET/BTI). A package that overrides `CFLAGS` wholesale then keeps
  it. `check-hardening.py` stays.
- The gcc runtime coupling goes away too: compiler and `libstdc++` come from one source.
  That includes the version-node check and the "new major waits for sid" rule in
  `packages/gcc/env.sh`.
- **The compiler follows release tarballs. The runtime keeps following the branch**
  (`releases/gcc-N`, synced nightly). If every branch commit rebuilt the compiler, it
  would invalidate every package's cache every day.
- Order: glibc (seed gcc) → binutils + gcc → glibc again with our gcc, so that everything
  in the image is compiled by our compiler. No three-stage bootstrap: one pass compiled by
  a recent seed is enough.
- GMP, MPFR and MPC stay seed-only (`deps.txt`, or in-tree). Debian's clang stays for
  systemd's BPF programs: BPF bytecode links against nothing, so its version barely
  matters. Upstream's LLVM release binaries are the alternative to Phase 1's approach if
  that changes.

**Effort**: about a week. **Build time**: gcc moves from parallel with the packages to
before them. It adds roughly 25 minutes to the critical path of a full CI build: binutils
in parallel with glibc, gcc ~18 min, a second glibc ~4.5 min. Today that path is glibc →
gcc → rootfs → boot, about 80 minutes for the whole run. Locally the extra work is small
(binutils, and the second glibc), since gcc is already compiled.

**Ongoing, and this is the real cost**: a new gcc major every spring, which we hit before
Debian has absorbed it. GCC 14 made implicit declarations errors, and GCC 15 switched to
`-std=gnu23` and broke `bool` in older autotools packages. Debian's mass rebuilds used to
find these for us; on our own, budget a few days per major, fixed with patches upstream
has already merged.

## Phase 3: our own headers and libraries

Drop the `-dev` section of `deps.txt`. Each package compiles against the staged output of
the packages it depends on. This is [extension-dependencies.md](extension-dependencies.md)
applied to the base: `DEPENDS=` in `env.sh`, a per-build sysroot assembled from
dependencies' staging trees, relocated `pkg-config`.

- Kernel UAPI headers come from `make headers_install` on our kernel tarball (seconds, no
  kernel build) and become glibc's first dependency.
- With Phase 2's sysrooted compiler and no Debian `-dev` installed, configure cannot find a
  library we do not ship. Silent break 2 goes away, and `known-missing-libs.txt` empties.
- What goes: the "≥ sid's" rules for OpenSSL, glib and the gcc runtime;
  `header-skew.py`; most of `docs/guides/linking.md`. OpenSSL 4 becomes our decision.
- Cycles to break by configuring out: util-linux ↔ systemd, dbus ↔ systemd, pam ↔ systemd
  (pam first).
- **CI becomes a graph.** The longest chain is about six levels: zlib → xz/zstd → elfutils
  → libbpf → systemd → crun. Each level is a job with its own startup and artifact
  download. Cache keys include dependencies' keys transitively, so a zlib bump rebuilds
  most of the image. That costs less than it sounds: glibc is already in every key and
  syncs nightly, so most days rebuild everything anyway.

**Effort**: 2–4 weeks, most of it the workflow (generated levels, artifact plumbing,
transitive keys) and breaking the cycles. **Build time**: another ~10–20 minutes on the
critical path, mostly per-level job overhead. An incremental build of a leaf package is
unchanged.

## Not recommended: building the build tools

GNU make, autotools, meson, ninja, cmake, perl, python, bison, flex and texinfo do not
reach the image. Building them would cost hours of work per bump and gain nothing visible.
Two exceptions are worth building when needed, because their version does shape the
output: **pahole**, whose BTF features follow the kernel (it is small and uses our
elfutils), and **meson** when systemd requires a newer one (pure Python, so the upstream
tarball can run in place).

After Phase 3, move the seed from sid to **Debian stable** instead. A seed that only
builds gcc and runs build tools wants stability, not freshness. The dated sid snapshot
bumps (`bump-snapshot.sh`, `snapshot-changes.py`) then become a bump at each stable point
release.

## Summary

| Phase | Effort | Full CI build | Removes |
| --- | --- | --- | --- |
| 1 Go, Rust upstream | 1–2 days | +0 | waiting for sid's Go/Rust |
| 2 binutils, gcc | ~1 week | +~25 min | gcc runtime coupling; wrong-glibc and lost-hardening failure modes |
| 3 own headers | 2–4 weeks | +~10–20 min | OpenSSL/glib "≥ sid" coupling; missing-library failure mode |
| seed → stable | ~2 days | +0 | sid snapshot churn |

A full build goes from about 80 to about 115–125 minutes. Incremental builds barely
change, except that a bump of a deep library or the compiler rebuilds everything above it.

**Recommendation**: do Phase 1 now. It is cheap, it is independent, and it fixes the
coupling most likely to block an update. Do Phase 2 next: most of the work is already
done in `packages/gcc`, and two of "the three things that break silently" stop being
possible. Do Phase 3 only when the "≥ sid" coupling actually blocks something, such as
OpenSSL 4, a glib that sid has not taken, or a Go/Rust package needing a newer C library.
Each phase must keep the reproducibility, signature and license guarantees as they are.
