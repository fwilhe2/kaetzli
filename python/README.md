# python/

The `kaetzli` package: the code behind the Python scripts in `test/` and `tools/`, its
tests, and the one lock file for all of them. None of it ever reaches an image.

```
launch.py            what each entry point calls: stdlib only, keeps .venv equal to the
                     lock and runs the module there
requirements.in      the dependencies, each argued; requirements.txt is its hashed lock
lock.sh              requirements.in → requirements.txt, on Python 3.12 in a container
src/kaetzli/         shared: repo (env.sh through bash), report (stdout + job summary),
                     gh (CI runs and artifacts), http_cache
src/kaetzli/cve/     the CVE reports: scan, context, history, and what they share
src/kaetzli/size/    the size reports: budget (rootfs-size), slim (vs-debian-slim),
                     history (size-history), trim (oci-trim), and sizes, their parser
src/kaetzli/elf/      the library checks: deps (check-rootfs-deps), versions
                     (check-symbol-versions), resolution (check-symbol-resolution),
                     and scan, what they read from a tree with pyelftools
src/kaetzli/sbom_check.py
                     test/check-sbom.py: what the SBOM says
src/kaetzli/kernel/  the kernel config checks: caps (kernel-caps), parity (kernel-parity),
                     config_diff (kernel-config-diff), and config, their .config reader
src/kaetzli/header_skew.py
                     tools/header-skew.py: the builder's -dev headers against our
                     libraries
src/kaetzli/updates/ the update check: upstream (what upstream released), release_notes,
                     check (check-updates), snapshot (check-snapshot), versort (GNU
                     sort -V) and http
src/kaetzli/automerge.py
                     tools/auto-merge.py: merging the update pull requests; in the shadow
                     of tools/auto-merge.sh until they have agreed for a while
src/kaetzli/qemu/    the qemu harness, test/qemu-lib.sh in Python: machine (the command
                     line) and console (the running guest, on pexpect)
src/kaetzli/boot/    the boot tests that use it: smoke (test/boot.py)
src/kaetzli/licenses.py
                     test/check-licenses.py: every package's LICENSE, against
                     test/dfsg-licenses.txt
tests/               pytest; fixtures/ holds real data cut down, and the bash versions'
                     output the Python had to reproduce
```

One Python file is outside the package: `image/sbom.py`, which writes the SBOM in the
assembly container. That has python3 and no venv, so it is stdlib only and imports
nothing from `kaetzli`; `tests/test_sbom_write.py` runs it the way the container does,
and the lint covers it as `../image/*.py`.

## Running

The host's `python3`, 3.12 or newer, with `venv` (on Debian and Ubuntu, the
`python3-venv` package; Fedora has it already). Nothing else: on first use `launch.py`
makes `python/.venv` with `python3 -m venv` and installs `requirements.txt` into it with
`pip install --require-hashes --no-deps`, which takes about fifteen seconds. When the
lock or the interpreter changes it makes the venv again from scratch. `src/` is on the
venv's path through a `.pth` file; the package itself is never built or installed.

```sh
./test/cve-scan.py output                  # an entry point, from any directory
cd python
python3 launch.py pytest                   # the tests, or any module, in the venv
python3 launch.py ruff check . ../image/*.py && python3 launch.py ruff format --check . ../image/*.py
```

**No uv**, here or in CI: it is one company's tool, and venv, pip and pip-tools do
the job (CLAUDE.md, *Host-side code*). **3.12 is the baseline**, in `pyproject.toml`,
ruff's target and `.python-version`; the Python workflow tests it and the newest
release.

## Dependencies

Never `pip install` anything by hand, into the venv or elsewhere. A dependency is a line
in `requirements.in`, argued there and in the pull request like a package (CLAUDE.md,
*Host-side code*), and a run of `./lock.sh`, which compiles `requirements.txt` with
pip-tools: every version pinned, every file hashed, on Python 3.12 in a `python:3.12`
container, with the pip-tools pinned in the lock. Existing pins are kept;
`./lock.sh --upgrade-package NAME` moves one. The Python workflow recompiles and fails
on any difference. Dependabot proposes updates to the lock once a release is two weeks
old; the Python workflow tests them.

## Adding a script

1. The code is a module under `src/kaetzli/` with a `main(argv)`, run through
   `kaetzli.report.run`, so a `Note` ends the report with exit 0.
2. The entry point in `test/` or `tools/` is the five lines `test/cve-scan.py` is.
3. Tests first: they fail, then the module makes them pass. A port of a bash script
   also gets a golden test: the bash version's output on fixtures, committed, which the
   Python must reproduce byte for byte, deliberate fixes excepted and said so.
