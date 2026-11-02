# Ideas from other image builders

**Status: proposal, partly implemented.** Implemented: sections 1–6; 7 as a report. This page collects features found
in other immutable or minimal Linux image builders that fit this project's constraints
(see `CLAUDE.md`), with enough detail to implement each one. Each section names where the
idea came from, what the gap is today, how to close it, and how to verify the change.

The sources were reviewed at these commits:

| Project | Repository | Commit |
| --- | --- | --- |
| Talos | <https://github.com/siderolabs/talos> | `a1920121c` |
| LinuxKit | <https://github.com/linuxkit/linuxkit> | `0e34a0074` |
| Flatcar mantle (kola) | <https://github.com/flatcar/mantle> | `35d87dfe8` |
| Flatcar scripts | <https://github.com/flatcar/scripts> | `3f50240ea1` |
| Flatcar security-triage | <https://github.com/flatcar/security-triage> | `0c71956` |
| podman-machine-os | <https://github.com/podman-container-tools/podman-machine-os> | `97c4a8c` |

Paths below are relative to each repository's root at that commit.

Sections 1–6 are small and independent; each is about one commit. Sections 7–9 are
larger. Section 10 feeds the [macOS container host](container-host.md) proposal, and
section 11 records what was considered and rejected, so it is not proposed again.

## 1. Scan every boot's console for kernel and crash messages

*Implemented:* `CONSOLE_BAD` and `console_check` in `test/qemu-lib.sh`. `test/verity.sh`
also exempts the panic's `Call Trace:`, and `test/sysext.sh`'s tampered boot the emergency
mode its failed mount ends in. `Call Trace:` is in the list from the start: KVM boots of
every qemu test were clean with it.

**Inspiration:** Flatcar mantle, `kola/harness.go` (the `consoleChecks` list, around
line 115). kola runs a list of regular expressions over the console of *every* test,
whatever the test is about, and fails the test on a match: `Oops:`, `WARNING: CPU: \d+
PID: \d+ at`, `Kernel panic - not syncing`, `SIGSEGV|=11/SEGV`, `[Cc]ore dump`, ext4
errors, `device-mapper: verity: ... data block ... is corrupted`, and emergency-shell
banners.

**Gap:** `test/qemu-lib.sh` defines only `QEMU_DIED` (`Kernel panic|Attempted to kill
init|Requesting system (poweroff|reboot)`), which detects a guest that is gone. A kernel
`WARNING:` or `BUG:`, a segfaulting daemon that systemd restarts, or a verity error on an
extension all leave the guest running, and no test fails unless a unit ends up failed.

**Implementation:**

1. In `test/qemu-lib.sh`, beside `QEMU_DIED`, define the patterns as a variable so a test
   can extend or override it:

   ```sh
   # What must never appear on a console, whatever the test. Matched once the test is
   # done, over the whole log. A test that provokes one of these on purpose sets
   # CONSOLE_BAD_EXEMPT to the pattern(s) it expects.
   CONSOLE_BAD='Oops:|BUG: |WARNING: CPU: [0-9]+ PID: [0-9]+ at |general protection fault|'\
   'Call Trace:|segfault at |=11/SEGV|[Cc]ore dump|EXT4-fs error|Remounting filesystem read-only|'\
   'device-mapper: verity: .* is corrupted|You are in emergency mode|Starting Emergency Shell'
   ```

2. Add a `console_check` function that greps `$LOG` (with `grep -aE`, the log has control
   characters), strips `\r`, drops lines matching `${CONSOLE_BAD_EXEMPT:-}`, and on a match
   prints the first few lines and calls `fail` with the description of the pattern.
3. Call it from the shared cleanup/exit path in `qemu-lib.sh`, so every test that sources
   it gets the check without editing each test. Only call it on the success path: a test
   that has already failed should report its own reason first.
4. `test/verity.sh` corrupts the image on purpose; set
   `CONSOLE_BAD_EXEMPT='device-mapper: verity'` there (and, if the kernel logs a stack
   for the I/O error, the matching part of that too).
5. `test/boot.py` runs `/bin/bash` as PID 1 and exits through a poweroff; check that it
   still reaches the cleanup path that runs the check.

**Verification:** run `test/systemd.py` and `test/podman.sh` on a good build: they must
still pass. Then prove the check works with a throwaway line in one test's guest
commands, `echo 'WARNING: CPU: 0 PID: 1 at fake' > /dev/kmsg`, which must fail the test
with the pattern's description. Do not commit that line. Since the check lives in the
harness rather than in a tree check, `test/selftest.sh` cannot cover it; this manual
step is its test.

**Caveats:** `Call Trace:` also appears for hung-task warnings, which are worth failing
on, but start without it if CI's slow TCG boots produce false positives, and add it once
a few runs are clean.

## 2. Fail on critical journal messages

*Implemented:* `CRIT_CHECK` in `test/systemd.py`, against
`test/known-journal-messages.txt`, which starts empty. It checks `-p err` (step 4) since
the commit after it: a KVM boot logged nothing at either priority.

**Inspiration:** podman-machine-os, `verify/image_test.go`, the test *"should have zero
critical error messages journalctl"* (around line 150), which runs
`journalctl -p crit -b` in the booted machine and requires empty output.

**Gap:** `test/systemd.py` asserts `is-system-running`, the taint string and the absence
of *Read-only file system*. It runs `journalctl -p err -b` only as part of `DIAGNOSE`,
after something already failed. A daemon that logs a critical error and keeps running
passes.

**Implementation:**

1. In `test/systemd.py`, add a check in the same style as `ROFS_CHECK`: run
   `journalctl -b -p crit --no-pager -o short`, count the lines, and print them prefixed
   with a marker (`CRIT> `) followed by `SYSTEMD-CRIT:[<n>]`, so the host side can match
   the count without matching its own typed command (see the `await` note in `CLAUDE.md`).
   Note `journalctl --grep` is unavailable (systemd is built without pcre2), so filter in
   bash as `ROFS_CHECK` does.
2. Add `test/known-journal-messages.txt`, an allowlist of extended regular expressions,
   one per line with a comment on each saying why it is accepted, in the style of
   `test/known-missing-libs.txt`. Filter the captured lines against it on the host.
3. Fail when any line survives the filter, naming the line.
4. Once `-p crit` is clean, consider tightening to `-p err` in a separate commit; expect
   to triage a few existing messages into the allowlist or into fixes.

**Verification:** a good build passes. `logger -p crit test` in the guest before the
check must make it fail.

## 3. Check what listens on the network

*Implemented:* `test/listeners.sh`, in the `boot` job. It found LLMNR and mDNS on every
interface, now off (`image/files/etc/systemd/resolved.conf.d/50-no-multicast.conf`). The
rounds are the base and the kubernetes extension: podman's starts nothing at boot, and
kubelet only listens once kubeadm has configured it.

**Inspiration:** Flatcar mantle, `kola/tests/misc/network.go` (`cl.network.listeners`,
around line 36), which compares the listening sockets of a booted machine to an expected
list; and LinuxKit, `test/cases/030_security/010_ports/`, a test that a default image has
no open ports.

**Gap:** nothing asserts which sockets the image opens. A version bump that enables a new
listener (an LLMNR or mDNS responder in resolved, a debug port in a daemon) would pass
every test, which is the kind of silent change `test/image-files/` exists to stop for
files.

**Implementation:**

1. Add `test/listeners.sh <image> <kernel>`, booting through `test/qemu-lib.sh` like the
   other tests. `ss` is in the base (iproute2). In the guest run
   `ss -Hlntup` and `ss -Hlnup`, and print each line with a marker.
2. On the host, reduce each line to `proto address:port process`, then drop sockets bound
   to loopback (`127.0.0.0/8`, `::1`) and resolved's stub addresses (`127.0.0.53`,
   `127.0.0.54`).
3. Compare what is left to an expected list kept in the test itself, which for the base
   should be only sshd on `:22` (both families). Fail on anything extra or missing, naming
   the socket and its process.
4. Run the check a second time with `SYSEXTS="podman"` and with the `kubernetes`
   extension, each with its own expected list (kubelet's `:10250`, for example), or fold
   the extension case into `test/sysext.sh`. Keep the lists next to the cases that use
   them.
5. Add the test to the `boot` job in `.github/workflows/ci.yml`, and to the command list
   in `CLAUDE.md`.

**Verification:** a good build passes; `systemd-socket-activate -l 8080 true &` (or a
`socat` from the debug extension) in the guest before the check must fail it, naming
port 8080.

**Caveats:** resolved's LLMNR listens on `:5355` unless disabled. If it shows up, the
right fix is `LLMNR=no` and `MulticastDNS=no` in a `resolved.conf.d` drop-in under
`image/files/etc/systemd/`, not an allowlist entry.

## 4. More checks on the assembled tree

*Implemented:* `test/check-tree-hygiene.sh`, run by `image/build-rootfs.sh` on both
flavours and covered by `test/selftest.sh`. Rather than a list of links that may dangle,
it does not judge a target under `dev`, `proc`, `sys`, `run`, `tmp` or `var`, which ship
empty. It found one real dangling link, systemd's `environment.d` link to
`/etc/environment`, now shipped empty.

**Inspiration:** Flatcar mantle, `kola/tests/misc/files.go` (`cl.filesystem`, around line
36): subtests `deadlinks`, `suid`, `sgid`, `writablefiles`, `writabledirs` and
`stickydirs`, each a `find` over the booted root.

**Gap:** `image/build-rootfs.sh` already fails on any setuid or setgid file (around line
247). It does not check for world-writable files, world-writable directories without the
sticky bit, or dangling symlinks; the dangling-link sweep it has only covers what the OCI
subtractions leave behind (around line 221).

**Implementation:**

1. In `image/build-rootfs.sh`, directly after the setuid/setgid check, add two more in
   the same form:

   ```sh
   # Nothing in the image is writable by everyone: the root is read-only, but the OCI
   # flavour is not, and a world-writable file there is writable by any container user.
   writable=$(find . -xdev ! -type l -perm -0002 ! \( -type d -perm -1000 \) -printf '%m %p\n')
   ```

   and fail with a message naming the files.
2. For dangling symlinks, run `find . -xdev -xtype l` *on the assembled tree*, after the
   trim and subtractions, and resolve absolute targets against the tree, not the
   container's own root (`find -xtype` follows absolute links from `/`). A small loop with
   `readlink` and a check under `$PWD` is safer than `-xtype` here. Some links are
   legitimately dangling in the image and resolve at boot (`/etc/resolv.conf` into `/run`,
   `/etc/mtab` to `/proc/self/mounts`, `/home` and `/root` into `var/`); list those in the
   check with a comment each.
3. Because `test/selftest.sh` tests checks in `test/`, consider putting both checks in a
   new `test/check-tree-perms.sh <tree>` that `build-rootfs.sh` calls, so a fixture can
   exercise it. Add two fixtures to `test/selftest.sh` using its `expect` helper: a tree
   with a mode-0666 file (must fail, naming the file) and one with a dangling link (must
   fail, naming the link), plus a good tree that passes.

**Verification:** both flavours still build; `test/selftest.sh` passes with the new
fixtures.

## 5. Hardening sysctls and kernel arguments

*Implemented:* `image/files/etc/sysctl.d/50-hardening.conf`, read back by
`test/systemd.py`. `kernel.kptr_restrict = 2` broke `perf probe` in `test/debug.sh` as
predicted, so it is 1. There is no `vm.unprivileged_userfaultfd` to set (`USERFAULTFD`
is off). Step 3 is in the kernel config, as preferred: `kspp.config` in
`packages/kernel/build.sh` turns on `SECURITY_YAMA` (for `kernel.yama.ptrace_scope`) and
`INIT_ON_ALLOC_DEFAULT_ON`, and turns off `SLAB_MERGE_DEFAULT`. Not `hardening.config`:
upstream has a fragment by that name in `kernel/configs/` and per arch, and `make` merges
them all.

**Inspiration:** Talos, `pkg/kernel/kspp/kspp.go` (`GetKernelParams`, the KSPP-recommended
sysctls Talos sets on every machine) and `pkg/machinery/kernel/kernel.go` (default kernel
arguments: `init_on_alloc=1`, `slab_nomerge`, `pti=on`); LinuxKit, `pkg/sysctl/etc/sysctl.d/`
(its default sysctl set, including the same security block). Both follow the
[Kernel Self Protection Project](https://kspp.github.io/Recommended_Settings).

**Gap:** `image/files/etc/sysctl.d/` holds three files, none about hardening, and the
command line from `rootfs.cmdline` and `test/qemu-lib.sh` carries no hardening arguments.

**Implementation:**

1. Add `image/files/etc/sysctl.d/50-hardening.conf`:

   ```ini
   # Kernel Self Protection Project recommendations, as Talos and LinuxKit set them, minus
   # the ones that would break rootless containers or the debug extension (see below).
   kernel.kptr_restrict = 1
   kernel.dmesg_restrict = 1
   kernel.yama.ptrace_scope = 1
   kernel.unprivileged_bpf_disabled = 1
   net.core.bpf_jit_harden = 2
   vm.unprivileged_userfaultfd = 0
   dev.tty.ldisc_autoload = 0
   dev.tty.legacy_tiocsti = 0
   fs.protected_symlinks = 1
   fs.protected_hardlinks = 1
   fs.protected_fifos = 2
   fs.protected_regular = 2
   fs.suid_dumpable = 0
   ```

   Every key must exist in this kernel, or systemd-sysctl logs an error at boot (which
   section 2 would then catch). `kernel.yama.*` needs `CONFIG_SECURITY_YAMA`; check
   `rootfs/boot/config` and add it to the kernel fragments if missing, or drop the line.
2. Deliberate differences from Talos, each worth a comment in the file:
   - `user.max_user_namespaces = 0` (Talos) is **not** set: rootless podman and
     `test/rootless.sh` need user namespaces (project constraint 3).
   - `kernel.kptr_restrict = 1` rather than Talos's `2`: at `2` even root sees zeroed
     addresses in `/proc/kallsyms`, which can break `perf probe` in `test/debug.sh`.
     Try `2` and keep it only if `test/debug.sh` passes.
   - `kernel.yama.ptrace_scope = 1` rather than `2`: `2` restricts ptrace to
     `CAP_SYS_PTRACE`, so `strace` and `gdbserver` would stop working for `user`.
   - `kernel.perf_event_paranoid` is left at the kernel default (`2`); `3` is a
     distribution patch that upstream kernels do not implement.
3. Add `slab_nomerge init_on_alloc=1` to the kernel arguments. The verity arguments are in
   `output/rootfs.cmdline`, written by `image/build-rootfs.sh`; put these there too, so
   every VMM and tool that reads that file gets them. Alternatively set
   `CONFIG_INIT_ON_ALLOC_DEFAULT_ON=y` and `CONFIG_SLAB_MERGE_DEFAULT=n` in the kernel's
   `guest.config` fragment, which needs no command line at all; prefer this, since the
   command line is supplied by the host and can be changed there.
4. Add `present` lines to a new `test/image-files/hardening.txt` for
   `etc/sysctl.d/50-hardening.conf`, so the file cannot be dropped silently.
5. Add a check to `test/systemd.py` (or the new listeners test) that reads a few of the
   values back with `sysctl -n` in the guest, because a shipped file that systemd-sysctl
   failed to apply looks the same as one that worked.

**Verification:** `test/systemd.py`, `test/rootless.sh`, `test/podman.sh`, `test/debug.sh`
and `test/k8s.sh` all pass. `test/debug.sh` is the one most likely to notice.

## 6. Limits that containers and Kubernetes need

*Implemented:* `image/files/etc/sysctl.d/50-containers.conf`, read back by
`test/systemd.py`. A sysrq crash with `kernel.panic = 10` still ends the test as "the
guest died". `kernel.pid_max` was already raised by systemd's own `50-pid-max.conf`.

**Inspiration:** Talos,
`internal/app/machined/pkg/controllers/runtime/kernel_param_defaults.go` (around lines
160–185: `fs.inotify.max_user_instances=8192`, `fs.aio-max-nr=1048576`,
`vm.overcommit_memory=1`, `kernel.panic=10`, `kernel.pid_max=262144`); podman-machine-os,
which ships a `10-inotify-instances.conf` and tests it is applied (`verify/image_test.go`,
around line 61); LinuxKit, `pkg/sysctl/etc/sysctl.d/` (`vm.max_map_count=262144`,
`fs.inotify.max_user_watches=524288`, `fs.aio-max-nr=1048576`).

**Gap:** none of these are set. The kernel default of 128 inotify instances per user is a
well-known failure on Kubernetes nodes and with many rootless containers (*too many open
files* from `kubectl logs -f`, kubelet or a container's file watcher), and
`vm.max_map_count` below 262144 makes common workloads (Elasticsearch, OpenSearch) refuse
to start. With no `kernel.panic`, a panicked node hangs forever instead of rebooting.

**Implementation:**

1. Add `image/files/etc/sysctl.d/50-containers.conf`:

   ```ini
   # Per-user inotify instances: the default of 128 is exhausted by kubelet and a few
   # dozen containers each watching files. Talos and podman-machine-os raise it.
   fs.inotify.max_user_instances = 8192
   # Memory map areas per process; 65530 is too few for Elasticsearch-style workloads.
   vm.max_map_count = 262144
   # Outstanding async I/O requests, for databases in containers.
   fs.aio-max-nr = 1048576
   # Reboot ten seconds after a panic instead of hanging. The tests run qemu with
   # -no-reboot, so a reboot still ends the test and is reported as the guest dying.
   kernel.panic = 10
   ```

   `fs.inotify.max_user_watches` is not needed: since Linux 5.11 its default scales with
   memory. `vm.overcommit_memory=1` is not proposed: it changes failure behaviour for
   every process, and kubelet sets what it needs itself.
2. Read the values back in a test, as in section 5, step 5.
3. Mention the file in the `ip_forward` neighbourhood of `CLAUDE.md` only if it turns out
   to have a pitfall; otherwise the comments in the file are enough.

**Verification:** `test/systemd.py`, `test/k8s.sh`, `test/node-conformance.sh`. Confirm
`kernel.panic` does not change how the `QEMU_DIED` tests report a panic.

## 7. An SBOM that vulnerability scanners can match

*Implemented, observing only:* `CPE=` in 53 packages' `env.sh` (two for gawk and shadow,
which NVD files under two vendors each), Go modules and Rust crates in the SBOM
(`builder/build-package.sh`, `image/sbom.py`), an SBOM per extension,
a package the assembly removes whole (the container image's kernel, kmod and PAM) left
out of that flavour's SBOM, and a `cve-scan` job that nothing waits on: `test/cve-scan.py`
(grype, pinned) and `test/cve-history.py`, which rescans the last builds' SBOMs with one
database so that a step in the count is the image's doing, and sums the steps by whether
the updater's commits made them. Step 4 (`check-sbom.py` requiring the references) is not done.

Known gaps:

- **No CPE** for conmon, cri-tools, libxcrypt, netavark, nftables and passt: as of
  2026-11-01 NVD's dictionary has no product for them and no CVE names one (netavark's
  one advisory is filed against Red Hat's products only). A first advisory will create
  one; the Go and Rust code in them is matched through its modules and crates meanwhile.
- **linux-tools** has none on purpose (its `env.sh` unsets the one it sources from the
  kernel's): it is the kernel's source and would count the kernel's CVEs twice.
- **No ignore list.** CPE matching reports disputed and distribution-specific advisories
  (glibc's CVE-2019-1010022–25, openssh's CVE-2007-2768), which inflate the count by a
  constant. Fine for a trend; an allowlist with a reason per entry, like
  `test/known-missing-libs.txt`, is the step before the count means anything absolute.
- **glibc fixes stay open until 2.45** (February 2027): upstream backports them to
  `release/2.44/master` but cuts no tarball, so building from that branch is a decision
  of its own.
- **History before this change** is the base image only (no extension SBOMs, no
  modules), with today's CPEs added by `cve-history.py`.

**Inspiration:** Flatcar security-triage, `src/*/sbom.py` (`SBOMIndex.from_spdx`, which
indexes Flatcar's production SPDX SBOM by package name and version), `go_vulndb.py` and
`rustsec.py` (which match Go modules and Rust crates against their advisory databases).
The tool exists because the SBOM is what advisories are matched against, so its
identifiers decide what can be found.

**Gap:** `image/build-rootfs.sh` (around line 726) gives every package the purl
`pkg:generic/<name>@<version>` and no CPE. Scanners such as grype, trivy and osv-scanner
can match very little of that. The Go and Rust programs (podman, CRI-O, kubernetes,
cri-tools, cni-plugins, netavark, aardvark-dns) embed hundreds of modules and crates that
the SBOM does not list at all, and these are where most advisories for this image would
come from.

**Implementation:**

1. **C packages: add CPEs.** Add an optional `CPE=` field to `packages/<pkg>/env.sh`, for
   example `CPE="cpe:2.3:a:openssl:openssl:$VERSION:*:*:*:*:*:*:*"`, carrying it like the
   other pins: `build.sh` passes it as `KAETZLI_*`, `builder/build-package.sh` writes it into
   the package's record, and `build-rootfs.sh` emits a second `externalRefs` entry with
   `"referenceCategory": "SECURITY", "referenceType": "cpe23Type"`. Take vendor/product
   pairs from the NVD CPE dictionary; leave the field out when NVD has none.
   `tools/bump-version.sh` needs no change because the CPE derives from `$VERSION`.
2. **Go programs: list their modules.** Every Go binary records its module graph, readable
   with `go version -m <binary>` (no execution needed). In `builder/build-package.sh`,
   after the install of a package that built Go, run it over the installed ELF files and
   write one `module=<path>@<version>` line per dependency into the package record. In the
   SBOM, emit each as a package with purl `pkg:golang/<path>@<version>` and a
   `CONTAINS` relationship from its program. The builder has a Go toolchain already.
3. **Rust programs: list their crates.** The crates are pinned in each package's
   `Cargo.lock` and present in `./vendor` (`cargo_vendored`). Parse `Cargo.lock` (its
   `[[package]]` blocks are line-oriented: `name =`, `version =`) and emit
   `pkg:cargo/<name>@<version>` the same way.
4. Extend `test/check-sbom.py` to require a CPE or a non-generic purl for each package that
   declares one, and at least one `pkg:golang` entry for each Go program.
5. Add a non-gating CI job beside `k8s` that runs osv-scanner (or grype) over
   `usr/share/kaetzli/sbom.json` from the rootfs artifact and writes the findings as a text
   job summary (the CI section of `CLAUDE.md` explains why it must be text). It reports
   only; do not make `result` depend on it.

**Verification:** `test/check-sbom.py` passes; the scan job lists findings for a known-old
module (temporarily pin one to check, then revert).

## 8. A report of what changed in the image

**Inspiration:** Flatcar scripts, `ci-automation/image_changes.sh` (`image_changes`,
`print_image_reports`) and `build_library/reports_util.sh` (`write_contents`,
`write_disk_space_usage_in_paths`). For every build Flatcar publishes the image contents,
package list, kernel config and disk usage, and diffs them against the last release or
nightly for review and release notes.

**Gap:** kaetzli already has most of the inputs — `output/rootfs-files-<flavour>.txt`
manifests, `test/kernel-config-diff.py`, `test/oci-trim.py`, `test/rootfs-size.py` and
`test/size-history.py` — but no single view of how a pull request changes the image
compared with `main`.

**Implementation:**

1. Add `test/image-diff.sh <old-output> <new-output>` that reports, as text:
   - package versions added, removed or changed (from the SBOMs, `usr/share/kaetzli/sbom.json`
     in each tree, or the package records);
   - paths added and removed per flavour (`diff` of the two manifests, grouped by the
     package that owns each path, which the staging trees can tell);
   - size changes per package above a threshold (reuse `rootfs-size.py`'s breakdown);
   - the extension contents the same way.
2. In CI, have the rootfs job fetch the previous `main` run's bundle (as
   `tools/fetch-image.sh` already does) and post the output as the job summary and, on
   pull requests, as a comment, the way the kernel job posts `kernel-config-diff.py`.
3. Reuse it in `tools/release-notes.py` once the [release](release.md) proposal is
   implemented, so release notes are generated from the same report.

**Verification:** a pull request that bumps one package shows exactly that package's
version, files and size; a docs-only change does not run it.

## 9. A kernel hardening configuration check

**Inspiration:** LinuxKit, `projects/kspp/` and the per-kernel config tests in
`test/cases/020_kernel/` (for example `022_config_6.12.x/`), which assert the shipped kernel
config has the hardening options LinuxKit requires; LinuxKit `docs/security.md`
("Modern and Securely Configured Kernels"); the KSPP recommended settings.

**Gap:** `test/kernel-caps.txt` lists what containers, Kubernetes, the VMMs and the debug
extension need, but nothing about hardening. The kernel is defconfig plus fragments, so
hardening options come and go with upstream defaults unnoticed.

**Implementation:**

1. Decide the list first, checked against what defconfig and the fragments produce today
   (`grep` over `rootfs/boot/config`). Candidates: `RANDOMIZE_BASE`, `STACKPROTECTOR_STRONG`,
   `HARDENED_USERCOPY`, `SLAB_FREELIST_RANDOM`, `SLAB_FREELIST_HARDENED`,
   `SHUFFLE_PAGE_ALLOCATOR`, `INIT_ON_ALLOC_DEFAULT_ON`, `STRICT_KERNEL_RWX`,
   `STRICT_MODULE_RWX` (moot with `MODULES` off), `SECURITY_YAMA`, `SECURITY_LOCKDOWN_LSM`,
   `BUG_ON_DATA_CORRUPTION`, `LIST_HARDENED`, `STATIC_USERMODEHELPER`, `VMAP_STACK`,
   `SCHED_STACK_END_CHECK`; and as must-be-off: `DEVMEM`, `DEVPORT`, `PROC_KCORE`,
   `LEGACY_TIOCSTI`, `COMPAT_BRK`, `LDISC_AUTOLOAD`. Some are amd64-only; mark the arch
   as `kernel-caps.txt` does for its existing entries.
2. Turn on what is missing in a fragment in `packages/kernel/build.sh` (a new
   `hardening.config` heredoc, merged before `vm.config`, so `build.sh`'s existing
   "every line applied" check covers it). Clearing `DEVMEM` and friends goes in
   `vm.config`.
3. Add the list to `test/kernel-caps.txt` with the second column saying what each protects
   against, following that file's rule that a failure names the feature. If it grows long,
   a separate list checked the same way is fine. Add a fixture to
   `test/selftest.sh` that drops one symbol and must fail naming it, as the existing
   `kernel-caps` fixture does.
4. Do not add `kernel-hardening-checker` to the builder for this: a short, explained list
   in the repository is easier to review than a third-party tool's whole policy, and a
   new `deps.txt` entry is a decision of its own.

**Verification:** both architectures build; the boot, container, debug and k8s tests pass.
Watch `test/rootfs-size.py` (randomization and init-on-alloc cost little space but some
boot time) and `test/debug.sh` (`PROC_KCORE` off affects `perf` only for kernel symbol
reads through `/proc/kcore`; BTF and kallsyms remain).

## 10. Additions for the macOS container host proposal

These belong to the work described in [container-host.md](container-host.md) and should be
done with it, not before.

- **Rosetta for x86-64 containers on Apple silicon.** podman-machine-os,
  `podman-image/rosetta-activation.sh` and `rosetta-activation.service`: when
  `ConditionVirtualization=apple` and `ConditionArchitecture=arm64` hold, mount the
  `rosetta` virtiofs tag that Virtualization.framework exposes and register it in
  `/proc/sys/fs/binfmt_misc/register` with the `F` (fix binary), `P` and `C` flags. The
  kernel already has `BINFMT_MISC` and `VIRTIO_FS`. systemd is built `-Dbinfmt=false`,
  so either ship a small oneshot unit like podman-machine-os does (it is a shell script,
  which constraint 5 allows) or enable systemd-binfmt and ship a `binfmt.d` file; the
  latter cannot do the mount, so the unit is simpler. vfkit needs `--device rosetta,...`
  in `tools/boot-vz.sh`.
- **Clock correction after host sleep.** LinuxKit, `pkg/host-timesync-daemon/`: a daemon
  listening on an `AF_VSOCK` port that, on each connection, reads `/dev/rtc0` and sets the
  system time, so the host can resync the guest after a suspend that leaves the clock too
  far off for NTP. This is the proposal's "Suspend and resume" step. Before writing such a
  daemon, test whether systemd-timesyncd recovers on its own after a vfkit suspend, and
  whether `CONFIG_PTP_1588_CLOCK_KVM` helps on the QEMU side; a new daemon is the last
  resort.
- **Trailing-newline fix for `/etc/subuid`.** podman-machine-os,
  `podman-image/fixup-subuid-subgid.service`, works around entries appended without a
  final newline. kaetzli ships both files read-only in `image/files/etc/`, so this matters
  only if users are ever added at runtime. Not needed today; listed so the issue is known.

## 11. Considered and not proposed

- **In-guest A/B updates** (Talos's installer, Flatcar's update_engine and
  `generate_payload`, systemd-sysupdate): the VMM supplies both the kernel and the root
  image from outside, so an update replaces files on the host, which `tools/vm.sh update`
  and `tools/fetch-image.sh` already do with verification. Signed artifacts are covered by
  the [release](release.md) proposal.
- **UKI, Secure Boot and TPM measurement** (Talos `internal/pkg/uki`, `internal/pkg/secureboot`,
  `internal/pkg/measure`; Flatcar `build_library/sbsign_util.sh`): they need firmware boot,
  an ESP and a bootloader, all deliberately absent. Revisit only with a self-bootable disk
  ([image variants](image-variants.md)).
- **Metadata and userdata agents** (LinuxKit `pkg/metadata`, `docs/metadata.md`; Ignition;
  cloud-init): systemd credentials over the kernel command line and qemu's fw_cfg already
  carry per-machine configuration, without a guest agent.
- **Hypervisor guest agents** (LinuxKit `docs/virtual-os-tools.md`, `pkg/qemu-ga`,
  `pkg/open-vm-tools`; podman-machine-os's vsock qemu-guest-agent): a new daemon with no
  current consumer. Reconsider if the container-host launcher needs one.
- **Many output formats** (Talos `pkg/imager/profile/output.go`: ISO, raw, qcow2, VHD,
  OVA; Flatcar `image_to_vm.sh`; LinuxKit `build --format`): they assume a bootable disk
  image or hypervisors this project does not target.
- **`fstrim` after container deletion** (LinuxKit `pkg/trim-after-delete`): the `/var` disk
  is ext4 on virtio; if sparse disk images become a concern, systemd's `fstrim.timer` (from
  util-linux) or the `discard` mount option is the simpler route.
- **Extension compatibility by version range** (Talos `pkg/machinery/extensions/validate.go`,
  semver constraints in each extension's manifest): `SYSEXT_LEVEL`/`CONFEXT_LEVEL` in
  `os-release` and `extension-release` already express the same contract, enforced by
  systemd itself.
