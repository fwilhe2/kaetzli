# What kaetzli is

An overview for people who already build Linux images: what kaetzli does, the choices it
makes, and how each one is enforced. Everything here is implemented on `main`. Details are in the [guides](README.md#guides).

## In one paragraph

kaetzli builds 64 packages from pinned upstream tarballs into a Linux userspace
(systemd, glibc, GNU coreutils, bash). It produces a read-only EROFS root on dm-verity for
VMs (QEMU, Firecracker, Apple's Virtualization.framework) and an OCI image of the same
userspace. Podman, Kubernetes and debugging tools are dm-verity-verified system extensions, not
part of the base. Each green build of `main` is a release, and upstream updates are merged
every night when CI passes. There are no stable versions and no backports.

## The build

**One toolchain, not a bootstrap.** Packages are compiled in a single builder image: a
Debian sid snapshot pinned by date. Unlike LFS, there is no cross-toolchain stage. glibc is
the exception: kaetzli builds its own, compiles every package against it as `--sysroot`,
and runs the builder itself on it. Other libraries are compiled against sid's `-dev`
headers and run against kaetzli's own builds. Three ELF checks guard that split:

- `check-rootfs-deps.py`: every `NEEDED` resolves inside the image.
- `check-symbol-versions.py`: every versioned symbol is defined by a shipped library.
- `check-symbol-resolution.py`: every unversioned undefined symbol resolves in the
  `NEEDED` closure.

See [Linking](guides/linking.md).

**Offline, pinned, content-addressed.** Each package declares its version, SHA-256 and
SPDX license in `env.sh`. All network access happens in one preparation step, which builds
or pulls a builder image and a sources image. Both are tagged by a hash of their inputs.
Package compiles run with `--network=none`. Patches (upstream's series for bash and
ncurses, glibc's release branch, project patches carrying upstream commits) apply with
`--fuzz=0`. A version bump to a release that already contains a patch fails until the
patch is removed.

**Optional dependencies are configured out, not installed.** A library that only the
builder has would compile fine and then fail at runtime in the VM. The fix is a configure
switch, not an entry in `builder/deps.txt`. The few exceptions are listed per library in
`test/known-missing-libs.txt`.

**Hardening is measured.** Every package gets Fedora's flag set: `_FORTIFY_SOURCE=3`,
stack protector, stack clash protection, CET/BTI, full RELRO, and PIE (Go with
`-buildmode=pie`). `check-hardening.py` reads the binaries, and the 14 known exceptions
are listed in `test/known-unhardened.txt`. Release builds are explicit: meson otherwise
defaults to `-O0`.

## The image

**Read-only root, verified per block.** The root is EROFS (zstd) with a dm-verity hash
tree appended to the same file. The root hash appears only in `rootfs.cmdline`, as a
`dm-mod.create=` table, so the kernel sets up verity without an initrd. A modified block
returns an I/O error; `test/verity.py` boots a tampered image to prove it.

**State is explicit.** `/etc` is part of the read-only root. `/var`, `/tmp` and `/run`
are tmpfs by default, and `/home` and `/root` point into `/var`. A disk attached as `/var`
is formatted on first boot and grown when enlarged. Because `/etc` cannot be written at
boot, the first-boot work runs during assembly: `systemd-sysusers`, `systemd-tmpfiles`,
`systemctl preset-all` and `ldconfig`. A package that writes to `/etc` at runtime shows up
as a journal error, and the boot test fails on it.

**Small because nothing unneeded is in it.** In the current release, the amd64 EROFS root
is 29.7 MB and the kernel is 11.4 MB. The trim removes only what nothing in the image can
reach: link-time files, docs, locales other than C, most gconv modules and terminfo, and
PAM/NSS modules that no configuration references. Each image has a size ceiling in
`test/size-budget.txt`, and the file's comments record why each ceiling changed.
Exceeding it fails CI, so growth requires a commit. Removals are guarded too: an `absent`
line in `test/image-files/` (68 today) fails the build if a version bump brings a removed
file back.

**The kernel is configured for VMs.** It starts from `defconfig` and `kvm_guest.config`,
and fragments subtract hardware and add container, Kubernetes, KSPP and tracing support.
`CONFIG_MODULES` is off. Kconfig drops fragment lines it cannot apply without warning, so
after merging, `build.sh` checks that requested `=y` symbols are set, that cleared symbols
stayed cleared, and that every named symbol exists. `kernel-parity.py` requires amd64 and
arm64 to have the same features. `kernel-caps.py` checks 108 symbols that container
engines need at runtime. The kernel tracks the latest stable series from its x.y.1
release.

**Few privileges by default.** Nothing in the image is setuid or setgid, and assembly
fails if anything is. Rootless helpers (`newuidmap`, `pasta`) get file capabilities, set
during assembly. Accounts have no passwords: shadow is all `*`, and a hash in it fails the
build. The console takes a `login.noauth` credential. SSH keys arrive as per-boot
credentials and are never read from a home directory. `systemd.py` compares each unit's
`systemd-analyze security` score against `test/unit-exposure.txt`.

**bash is the only interpreter.** There is no Perl, Python, Lua or JS runtime in the
image. `make install` regularly adds such scripts (iproute2's `routel`, OpenSSL's
`c_rehash`), and each package's build deletes them explicitly. `test/image-files/interpreters.txt`
fails the build if one ships.

**One userspace, two formats.** The OCI image is the disk tree with the kernel, systemd,
udev, PAM, sshd and kmod removed. Removed libraries are guarded, so a new consumer fails
the build. `oci-trim.py` reports the difference on every run.

**Extensions instead of a package manager.** Podman (with netavark, aardvark-dns, pasta
and shadow), Kubernetes (kubelet, kubeadm, CRI-O, CNI) and a debug set (strace, tcpdump,
gdbserver, perf, bpftool) are systemd-sysext images, each with its own dm-verity table and
SBOM. The base keeps the kernel features, crun with seccomp, conmon and nftables, so
`crun run` works without any extension.

## Supply chain

**Reproducible assembly, checked in CI.** Every timestamp comes from one
`SOURCE_DATE_EPOCH`, every tar goes through one normalising function, and only
`security.capability` xattrs are kept. CI assembles twice and compares the EROFS image,
command line, OCI archive, SBOMs and extensions byte for byte. `test/reproducible.sh`
builds individual packages twice from clean worktrees; it runs locally, not in CI.
Anyone can check a release: `tools/rebuild-release.py` rebuilds it from its tag in a clean
worktree and compares every asset of the host's architecture with the release's
`SHA256SUMS`.

**The SBOM is generated from the build, not scanned from the result.** Each package build
writes a record of its pins, patches with their hashes, and license. Assembly turns the
records that still own files in the assembled tree into SPDX 2.3. Go modules and Rust
crates are included as contained packages, NVD CPEs are attached, and CVEs fixed by
patches become VEX statements. `check-sbom.py` validates the result.

**Licenses are checked and shipped.** Every package must have a DFSG-free SPDX license
(`check-licenses.py`, its own workflow). Upstream license texts are copied into
`/usr/share/licenses/<pkg>/`, and assembly fails if any later step changes that tree. The
sources image holding every tarball and patch is referenced by digest in each release, as
the corresponding source.

**Provenance.** Builds on `main` attest the OCI images (SLSA provenance and SBOM) and every
file in the boot bundle. `tools/fetch-image.sh` installs nothing whose hashes do not match
the release's `SHA256SUMS` or the CI run's bundle manifest, and checks attestations when
`gh` is available.

**Secure Boot seals the root hash.** `tools/sign-uki.sh` builds a signed unified kernel
image: systemd's stub, the kernel, and the command line with the root hash. Under OVMF
with Secure Boot on (amd64), the firmware runs only that signed image, and the stub ignores
a command line passed from outside, so the root hash cannot be swapped. `test/secureboot.py`
boots the signed image with `init=/bin/bash` injected and checks that `/proc/cmdline` is
the sealed one, and that an unsigned image is refused. Not covered yet: arm64, extensions
and a `/var` disk in this mode, and a release signing key (the tool uses a throwaway key
unless given one).

## Release and update model

**Rolling, with no backports.** The update workflow checks upstream every day at 17:00
UTC and opens a pull request for each new release, patch series or glibc branch commit.
Pull requests that pass CI are merged automatically between 20:00 and 05:00 UTC. A `hold`
label excludes one. Each green build of `main` that changes what the build reads is
published as a GitHub release named `YYYYMMDD.HHMMSS-<run>`. A release's assets and tag are not changed after publishing,
each release descends from the previous one, and expire after at least 37 days, except the
latest. There will be no `1.0`. See [Releases](proposals/release.md).

**Caches expire.** No CI cache lives longer than about two weeks. On the 1st and 15th of
each month, `main` rebuilds from scratch, including the builder and sources images. A
build that only works because of a stale cache is caught within two weeks.

**Rollback on update.** The launcher (`kaetzli`, a static Go binary attached to each
release) and `tools/vm.sh` replace a machine's root while keeping its `/var`. If a new
snapshot does not come up (kernel panic, or no SSH before the timeout), the launcher
restarts the machine on the last snapshot that did and skips the failed one from then on.
`test/launcher-rollback.py` tests this in CI.

## Testing

**Every boot is a test.** Most boot tests boot systemd under QEMU with KVM when the runner
has it. They cover networking (DHCP, DNS, TLS with `curl`), SSH, crun, Podman with
seccomp and Quadlet, rootless Podman, persistent `/var`, extensions, verity tampering,
Firecracker- and Virtualization.framework-shaped device sets, listening sockets, and the
debug tools. `systemd.py` requires the `running` state, no taint, and no journal message
at `err` or above other than the 8 listed in `test/known-journal-messages.txt`. Every
console is scanned for warnings, oopses, segfaults and emergency mode. Binaries built on
the host in C, C++, Rust and Go are run in the image (`prebuilt.sh`). A three-node
kubeadm cluster runs on every build, and the upstream `[NodeConformance]` node e2e tests
run when packages, the image or the harness change. Neither gates the result.

**The checks are tested.** `test/selftest.sh` runs each tree check against fixtures broken
in a known way, and requires each check to fail and name the problem. A check that skips
what it cannot judge would otherwise pass without checking anything.

**Reports, not gates.** Some outputs are informational: grype CVE scans with EPSS, KEV
and distribution verdicts, a CVE workbook in each release, size and boot-time history,
comparison with `debian-slim`, and a package and size diff against the previous release.
`kernel-config-diff.py` posts newly enabled `default y` drivers on kernel bump pull
requests.

## What it is not

- **Not a self-hosting LFS.** The toolchain is Debian's, permanently.
- **Not for hardware.** It targets VMs only, and the kernel has no drivers beyond that.
- **Not a package-managed distribution.** Software is added as a package build or a
  system extension.
- **Not supported per version.** If you cannot run the latest build, kaetzli is not for
  you.
- **Coupled to Debian sid.** OpenSSL, glib and the gcc runtime must stay at or above
  sid's versions. Checks flag skew; they do not remove it.
