# Host-side scripts: which language for which job

**Status: proposal, partly implemented.** Implemented: items 1 to 5, the CVE reports,
the SBOM, the size reports, the library checks and the policy checks, and item 6, whose
`auto-merge.py` is in the shadow of `auto-merge.sh` (`python/`, see its README). Item 7
has begun: the harness, `kaetzli.qemu`, and its first clients, `test/boot.py`,
`test/systemd.py` and `test/network.py`. CLAUDE.md (*Host-side code*)
allows Python and Go for what runs on the host, in CI or in a builder container. This
page reviews the scripts against that rule and orders the migration.

Bash remains the only interpreter *in the image*. This is about the repository's tooling.

## The test applied

A script stays bash when its job is running commands and checking exit codes. It moves to
Python when most of its lines work around bash: jq programs longer than the shell around
them, awk for arithmetic or tables, `declare -A` as a data structure, `sed` as a JSON
escaper, `xargs -P` with `export -f` for concurrency, hand-parsed tool output, or argument
parsing and state for a CLI with subcommands.

Go was considered for each and chosen for none: nothing here has to run where Python
cannot (Python 3.12 or newer is on every distribution the tooling targets), and two
general-purpose languages beside bash would be more to maintain, not less.

## Move to Python, by payoff

### 1. CVE reports: `test/cve-context.py`, `cve-scan.py`, `cve-history.py` (done)

Ported with no dependency beyond the stdlib, to stay byte-identical; `python-debian`
came after, as a change of its own: Debian's fixed versions compare in dpkg's order, so
`2026a` is before `2026b` and `1.0~rc1` before `1.0`, which the digits alone did not
say. What the bash versions said:

`cve-context.sh` is about 280 lines, most of them a jq program that joins five sources,
plus a curl cache and `xargs -P 8 bash -c` with exported functions. Its Debian version
comparison is a regex approximation (`sub("^.*\\+really"; "")`) that `python-debian`'s
`Version` does properly. In Python it is dataclasses, `requests` with a cache, a thread
pool and one join. `cve-scan.sh` and `cve-history.sh` share the run and artifact
lookups and the summary format, so they move with it.

`cve-workbook.sh` stays as it is: it is a wrapper around grind and its `.rhai` script.

Dependencies: `requests`, `python-debian`.

### 2. SBOM: `image/sbom.py`, `test/check-sbom.py` (done)

Ported with no dependency beyond the stdlib, in two places. The writer is
`image/sbom.py`, run on python3 in the assembly image (builder-side, like perl in the
builder), stdlib `json` only, so it stays outside the `kaetzli` package; the checker is
`kaetzli.sbom_check`. The checks were the bash version's; validation against the SPDX
2.3 model with `spdx-tools` came after, as a change of its own, and found two things
the bash writer got wrong: `fix` locators in download-location syntax rather than URLs,
and a `LicenseRef-` with no text (`hasExtractedLicensingInfos`). What the bash versions
said:

`sbom.sh` wrote SPDX with `printf`, and `jstr` escaped strings with `sed`. It did not
escape control characters other than tab and CR, and every field was a place to forget a
comma. `check-sbom.sh` existed largely to catch that, and its comment named a JSON
serialiser in `image/Containerfile` as the cost being avoided.

Dependencies: none in the assembly image; `spdx-tools` for the check.

### 3. Size reports: `test/rootfs-size.py`, `vs-debian-slim.py`, `size-history.py`, `oci-trim.py` (done)

Ported with no dependency beyond the stdlib, as `kaetzli.size`: one parser for the size
report `image/build-rootfs.sh` writes, shared by all four, and the run lookup from item 1
(`kaetzli.gh.Gh`) for the history. The measurements stay commands: debian-slim is still
`podman export`, `du` and `find`, the layer is unpacked by GNU tar, and the links check is
still `test/check-rootfs-deps.sh`. One deliberate difference: `oci-trim`'s count of
removed subtrees. In byte order `systemd-boot` sorts between `systemd` and `systemd/…`,
and the awk that found the topmost path of each subtree then counted `systemd`'s
contents as subtrees of their own. What the bash versions said:

These were awk sparklines, associative arrays of directory sizes and markdown tables, and
`size-history.sh` repeated the "last N green runs on main that still have artifact X"
loop that `cve-history.sh` had too.

Dependencies: none (stdlib and the `gh` CLI).

### 4. Static ELF checks: `test/check-rootfs-deps.py`, `check-symbol-versions.py`, `check-symbol-resolution.py` (done)

Ported as `kaetzli.elf`: one scan of the tree with `pyelftools` (`scan`), shared by the
three checks, and a check each (`deps`, `versions`, `resolution`). The tests compile
`selftest.sh`'s trees and one more for the longer reports, and the bash versions' output
on them is the golden. On CI's own trees (amd64 and arm64, the base and each extension
merged over it) and on a copy broken three ways (a stub libz with one version,
libpcre2-8 deleted, libtinfow emptied behind the libtinfo.so.6 link), the output is
identical, in one to eight seconds where bash took 15 to 30. `selftest.sh` runs the `.py` checks, and moves
to pytest when the checks it covers are all Python (*Later*). Deliberate differences:

- The scan reads the symbol table, not readelf's columns. On arm64 readelf prints
  `[VARIANT_PCS]` after the visibility of an SVE or Advanced SIMD function, which moved
  every column after it by one: the bash version took a definition's section number for
  its name (libmvec's vector functions were defined as `13`), and would have taken an
  undefined one for a definition of `UND`. Nothing on arm64 calls one today, so CI's
  output is the same; the first caller would have been reported unresolved.
- `check-rootfs-deps` follows a library's symlink inside the tree; readelf followed an
  absolute one on the host.

What the bash versions said:

They parse `readelf` text with awk and compute NEEDED closures in shell. `pyelftools`
reads the dynamic section, version tables and symbols directly, with no text format to
misparse. The first version of `check-symbol-resolution.sh` was wrong in the way
`selftest.sh` describes, which is the kind of mistake this rewrite removes. The fixtures
in `selftest.sh` make it safe: every finding has to reproduce, by name. They run on the
CI runner, not in the assembly image.

Dependencies: `pyelftools`.

### 5. Policy checks: `check-licenses.py`, `kernel-caps.py`, `kernel-parity.py`, `kernel-config-diff.py`, `header-skew.py` (done)

`check-licenses.py` is `kaetzli.licenses`. The bash version split SPDX expressions on
whitespace and parentheses, so `MIT ISC`, `MIT AND`, `(MIT` and `GPL-2.0-only WITH MIT`
all passed; `license-expression` parses them, and SPDX's list says which identifiers are
exceptions, so each of those is now NOT VALID. On the repository's own packages the
output is identical, but for `glib` and `glibc`, which the bash glob ordered by the
host's locale.

The kernel scripts are `kaetzli.kernel`: one reader of a `.config` (`config`) and a
check each (`caps`, `parity`, `config_diff`). Their lists moved out of the scripts'
heredocs into `test/kernel-caps.txt` and `test/kernel-parity-allowed.txt`, beside the
other lists in `test/`. On CI's 7.3.1 configs for both arches and amd64's 7.2.13 the
output is identical, and `config_diff`'s classification of the 7.3.1 source agrees with
the awk's on all 21,742 symbols. One deliberate difference: `kernel-config-diff` lists
changes by symbol, a removal before its addition, where GNU diff's hunks could put a
symbol's old and new values lines apart.

`header-skew.py` is `kaetzli.header_skew`: the table is a dict, the version is stripped
and compared in Python (`python-debian`'s `Version`, dpkg's order), and the builder is
asked only for what it has, `dpkg-query`'s versions. Against the builder its output is
identical; synthetic versions through a stub `dpkg-query` in the builder, compared by its
real `dpkg --compare-versions`, are the edge cases' golden.

Dependencies: `license-expression`.

### 6. Updates and GitHub automation: `tools/upstream.py`, `check-updates.py`, `release-notes.py`, `check-snapshot.py` (done), `auto-merge.sh`

`upstream.sh` scrapes HTML listings with regexes and sorts versions with knobs from
`env.sh`. `auto-merge.sh` is a state machine that pushes to `main`: age, labels,
checks, rebase, rebuild, fast-forward. It is the script where readability matters most
and where a rewrite is riskiest, so it goes last in this group, after `-n` (dry-run)
output can be compared line by line against the bash version over several nights.
`UPSTREAM_SED` values are sed expressions and stay that way: Python runs `sed -E` on them
rather than translating the knob.

`upstream.py`, `release-notes.py` and `check-updates.py` are `kaetzli.updates`, with no
dependency beyond the stdlib (`requests` was not needed for GETs and HEADs). The env.sh
patterns are matched by Python's `re`, which reads every one of them as ERE does;
`sort -V` is gnulib's `filevercmp`, ported and tested against GNU sort on the real
versions and 3,000 random strings; a candidate's URL is still env.sh evaluated by bash.
Run live over every package, side by side with the bash versions, `--json` was
byte-identical, the text identical but for glib and glibc (the bash glob's locale), and
`upstream.py` gave every package and vendor tarball the same answer, and a plain error
for `kaetzlifetch`, whose local source crashed the bash version. Packages are checked
eight at a time: four minutes became forty seconds. `check-snapshot.py` (Docker Hub's
dated sid tags against the pin) moved with them, and was identical live.

`auto-merge.py` is `kaetzli.automerge`: the decisions (the checks' state, the night
window, which pull requests are updates) are functions, and every git and gh command is
the one the bash version ran, in the same order. Its tests run both versions through
fake `git`, `gh` and `date` on nine scenarios, dry and for real: the merge, a rebase
pushed with a lease, a lease refused, a conflict, CI red after a rebase, the branch or
main moving meanwhile, stale and missing checks, and the window's edges. The Python
reproduces the bash version's output, exit code and the log of every command. On the
open pull requests, live, the dry runs were identical (with `MIN_AGE_HOURS=0` as well).

It does not merge yet. `auto-merge.yml` runs both with `-n` before the bash version acts
and warns, with the diff in the job summary, when they differ. When they have agreed for
two weeks of nights with something to do, the workflow runs `auto-merge.py` and
`auto-merge.sh` and the shadow step are deleted; the harness and its goldens stay as
the tests.

`dispatch-ci.sh` was on this list and stays bash: it is `gh` and `git` in sequence (a
check run, a dispatch, a wait), and its two `jq` expressions are one-liners.

`bump-version.sh` and `bump-snapshot.sh` stay bash: they are `sed -i` on `env.sh` and a
download.

Dependencies: none so far.

### 7. The qemu harness and its clients: `test/qemu-lib.sh`, the boot tests, `tools/vm.sh`, `tools/k8s-demo.sh`

The biggest payoff and the biggest job. `qemu-lib.sh` drives a console through a fifo
with `await`, marks and timeouts, which is what `pexpect` does. The marks rule in
CLAUDE.md exists because of a bug that a pexpect-based harness would not allow. `vm.sh`
(641 lines) and `k8s-demo.sh` (520) are CLIs with subcommands, config files and state,
and `argparse` plus dataclasses would make them much shorter. With `pytest`, the ~17 boot
tests become test functions sharing fixtures (a booted machine, an ssh session), and
`CONSOLE_BAD` becomes a fixture finaliser.

Two constraints shape it. `tools/boot-vz.sh` runs on macOS and sources `qemu-lib.sh`;
it can keep a small bash copy of what it needs or run the Python harness on macOS's own
python3 (uv, which would have brought one, is ruled out: CLAUDE.md, *Host-side code*). And
`tools/boot-qemu.sh` has to keep booting *the same machine* as CI, so the harness must
expose its argv-building for interactive boots. Port it after 1–6 have settled the
module layout, and keep the bash and Python harnesses side by side until every boot test
has moved.

Begun. `kaetzli.qemu` is the harness: `machine` builds the command line (qemu_setup,
qemu_preflight, qemu_ssh_setup, qemu_var_disk, qemu_sysexts, qemu_argv) and `console`
drives the guest (qemu_boot, await, console_login, fail, console_check) through
pexpect's `PopenSpawn`, on pipes as the fifo was, so qemu sees no tty. Every wait sees
only what came after the last match or `mark()`, so console_mark has nothing to forget.
`test/boot.sh` is `test/boot.py` (`kaetzli.boot.smoke`), and on CI's image its output,
statuses and exit codes were the bash version's in every case (a shell, no prompt, a
deaf console, a dead init), in two seconds where bash, polling every two, took four (with KVM).

The bash harness stays until the last test has moved, and the two are held together
live: `test_qemu_machine.py` runs `qemu-lib.sh` on 14 shapes of machine (each profile,
both arches, extensions, a `/var` disk, fw_cfg, ssh with a forward and a proxy) and on
each refusal, and requires the same binary, argv, ssh_config and error lines, and the
same QEMU_DIED, CONSOLE_BAD and login strings. Writing it found one difference to keep:
an empty `QEMU_DISPLAY_ARGS=()` is unset to bash's `${x+set}`, so it keeps
`-nographic`. The console is tested against a fake qemu (`tests/fixtures/qemu/`): a
shell, a deaf console, a panic, a getty that drops what is typed at first.

`test/systemd.sh` is `test/systemd.py` (`kaetzli.boot.systemd`), the first user of
`login`. Its golden is the bash version against the fake qemu, one guest per way the
test can fail (degraded, tainted, writes to the read-only root, journal errors, a sysctl,
fw_cfg, kaetzlifetch), console tail included, and the Python reproduces all eleven byte
for byte. On CI's image the two agree, in eight seconds where bash took 25. Two
deliberate changes in the harness came with it: `mark()` lets the console settle for a
quarter second first, so a prompt still on its way is not read as the next command's
output, and every result is read from the match that ended the wait, not from the last
line of the whole log that happens to look like one.

`test/network.sh` is `test/network.py`, with `Guest.retry` (type, wait, type again until
the deadline) for its two rounds; its golden is four guests (clean, no network, no
tools, a panic), and on CI's image it agrees in eight seconds where bash took 21.

Next: the rest of the systemd-booting tests, one per pull request; then `tools/boot-qemu.sh` takes its command line from
`kaetzli.qemu` (`boot-vz.sh` keeps what it needs of the bash, for macOS); then `vm.sh`
and `k8s-demo.sh`. Whether the tests become pytest functions sharing a booted machine
is decided when there are enough of them to share one.

Dependencies: `pexpect` (and `ptyprocess` through it), `pytest`.

### Later, or when touched

- `test/selftest.sh`: becomes pytest once the checks it exercises are Python. Until
  then it stays bash and tests both.
- `test/check-doc-links.sh`, `check-image-files.sh`: small and fine, but they parse
  formats (markdown, glob manifests) that Python handles better. Move when touched.
- `image/build-extension.sh`: mostly commands; its SBOM is `image/sbom.py` now.

## Stay bash

| script | why |
| --- | --- |
| `build.sh`, `builder/build-package.sh`, `packages/*/build.sh`, `env.sh` | sourced by package builds; the vocabulary is shell functions, and `env.sh` is read by bash everywhere |
| `image/build-rootfs.sh` | mostly cp, tar, setcap, mkfs.erofs and veritysetup in order. 907 lines is too long; split the reports out (item 3, as the SBOM was) and leave the commands |
| `tools/lib.sh` | the bash scripts that remain still need it; the Python module is its counterpart, not its replacement |
| `tools/prep.sh`, `fetch-sources.sh`, `build-image.sh`, `build-extension.sh`, `publish-oci.sh`, `merge-staging.sh`, `clean.sh`, `image-tags.sh`, `build-node-e2e.sh` | podman, tar and file operations in sequence |
| `tools/dispatch-ci.sh` | `gh` and `git` in sequence: a check run, a dispatch, a wait for the run |
| `tools/check-patches.sh`, `sync-patches.sh`, `patch-cves.sh` | patch and git invocations; `sync-patches.sh` is the largest and could move later |
| `tools/boot-qemu.sh`, `boot-firecracker.sh`, `boot-vz.sh`, `ssh.sh`, `demo.sh` | thin wrappers that exec a VMM or ssh; `boot-vz.sh` must run on macOS's bash 3.2 |
| `test/check-tree-hygiene.sh` | runs inside the assembly image; a few `find` calls |
| `test/reproducible.sh`, `prebuilt.sh`, `oci.sh` | podman and git orchestration; they move only if the harness (item 7) gives them something |
| inline `run:` steps in `.github/workflows/` | short; a step that outgrows a few lines becomes a script, and then this page applies |

## How a migration lands

One script per pull request, behaviour unchanged. The proof is the same output before
and after: byte-identical reports, SBOMs and job summaries for the same inputs, the same
exit codes, and every `selftest.sh` fixture still failing with what is wrong named. Then
the bash file is deleted in the same pull request and the `Commands` list in CLAUDE.md is
updated. The CI step that runs it gains `actions/setup-python` on the 3.12 baseline, as
`ci.yml`'s `&setup-python` does.
