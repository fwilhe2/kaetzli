"""test/check-hardening.py: every package's binaries carry the flags builder/build-package.sh
sets — RELRO, BIND_NOW, PIE, no executable stack, a stack protector, FORTIFY_SOURCE.

The staging trees are compiled here from a few lines of C with the host's gcc, one
package per way a build can drop the flags, the same as test/selftest.sh does for its
copy of these cases.
"""

import io
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

import elf_trees
import pytest

from kaetzli.elf import hardening

needs_gcc = pytest.mark.skipif(not elf_trees.available(), reason="needs gcc and readelf")

# A local array (so -fstack-protector-strong instruments main) and a printf with an
# argument, which with _FORTIFY_SOURCE >= 2 always becomes __printf_chk.
APP_C = """
#include <stdio.h>
#include <string.h>
int main(int argc, char **argv) {
    char buf[64];
    strncpy(buf, argc > 1 ? argv[1] : "x", sizeof buf - 1);
    buf[63] = 0;
    printf("%s %d\\n", buf, argc);
    return 0;
}
"""
START_C = "void _start(void) { for (;;); }\n"

GOOD = [
    "-O2",
    "-U_FORTIFY_SOURCE",
    "-D_FORTIFY_SOURCE=3",
    "-fstack-protector-strong",
    "-fPIE",
    "-pie",
    "-Wl,-z,relro",
    "-Wl,-z,now",
    "-Wl,-z,noexecstack",
]

# package -> the flags its one program is built with: GOOD, with one thing undone.
BROKEN = {
    "lazy": [*GOOD, "-Wl,-z,lazy"],
    "norelro": [*GOOD, "-Wl,-z,norelro"],
    "nopie": [*GOOD, "-fno-PIE", "-no-pie"],
    "execstack": [*GOOD, "-Wl,-z,execstack"],
    "nossp": [*GOOD, "-fno-stack-protector"],
    "nofortify": [*GOOD, "-U_FORTIFY_SOURCE"],
}
FINDING = {
    "lazy": "bind-now",
    "norelro": "relro",
    "nopie": "pie",
    "execstack": "exec-stack",
    "nossp": "stack-protector",
    "nofortify": "fortify",
}


def _gcc(out: Path, c: str, flags: list[str]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    src = out.with_suffix(".c")
    src.write_text(c)
    subprocess.run(["gcc", *flags, str(src), "-o", str(out)], check=True, capture_output=True)
    src.unlink()


def _staging(root: Path, packages: dict[str, list[str]]) -> Path:
    for pkg, flags in packages.items():
        _gcc(root / pkg / "rootfs" / "usr" / "bin" / pkg, APP_C, flags)
    return root


def check(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = hardening.main(argv)
    return rc, buf.getvalue()


@pytest.fixture
def empty(tmp_path) -> Path:
    p = tmp_path / "allowlist.txt"
    p.write_text("")
    return p


@needs_gcc
def test_a_hardened_package_passes(tmp_path, empty):
    staging = _staging(tmp_path / "staging", {"good": GOOD})
    rc, out = check([str(staging), str(empty)])
    assert rc == 0, out


@needs_gcc
@pytest.mark.parametrize("pkg", sorted(BROKEN))
def test_each_missing_flag_is_named_with_its_package(tmp_path, empty, pkg):
    staging = _staging(tmp_path / "staging", {"good": GOOD, pkg: BROKEN[pkg]})
    rc, out = check([str(staging), str(empty)])
    assert rc == 1
    assert f"{pkg}: {FINDING[pkg]}" in out
    assert "good:" not in out


# Fortified, and passes fprintf's address on: an address cannot be fortified, so the plain
# symbol is imported beside the __*_chk ones. perf and gawk do this.
POINTER_C = APP_C.replace(
    "int main(int argc, char **argv) {",
    "int (*out)(FILE *, const char *, ...) = fprintf;\nint main(int argc, char **argv) {",
).replace("return 0;", 'out(stderr, "%d\\n", argc);\n    return 0;')


@needs_gcc
def test_a_fortified_file_that_takes_printfs_address_passes(tmp_path, empty):
    staging = tmp_path / "staging"
    _gcc(staging / "ptr" / "rootfs" / "usr" / "bin" / "ptr", POINTER_C, GOOD)
    rc, out = check([str(staging), str(empty)])
    assert rc == 0, out


@needs_gcc
def test_a_file_finding_names_the_file(tmp_path, empty):
    staging = _staging(tmp_path / "staging", {"lazy": BROKEN["lazy"]})
    _, out = check([str(staging), str(empty)])
    assert "usr/bin/lazy" in out


@needs_gcc
def test_an_allowlisted_finding_does_not_fail(tmp_path):
    staging = _staging(tmp_path / "staging", {"nossp": BROKEN["nossp"]})
    allow = tmp_path / "allowlist.txt"
    allow.write_text("# why\nnossp stack-protector\n")
    rc, out = check([str(staging), str(allow)])
    assert rc == 0, out
    assert "nossp: stack-protector" in out  # still reported, as known


@needs_gcc
def test_the_allowlist_is_per_property(tmp_path):
    staging = _staging(tmp_path / "staging", {"nossp": BROKEN["nossp"]})
    allow = tmp_path / "allowlist.txt"
    allow.write_text("nossp fortify\n")
    rc, _ = check([str(staging), str(allow)])
    assert rc == 1


@needs_gcc
def test_a_stale_allowlist_line_is_reported(tmp_path):
    staging = _staging(tmp_path / "staging", {"good": GOOD})
    allow = tmp_path / "allowlist.txt"
    allow.write_text("good stack-protector\n")
    rc, out = check([str(staging), str(allow)])
    assert rc == 1
    assert "good stack-protector" in out


@needs_gcc
def test_a_stale_line_for_a_package_not_staged_is_left_alone(tmp_path):
    # CI checks one architecture's staging at a time, and a run may stage a subset.
    staging = _staging(tmp_path / "staging", {"good": GOOD})
    allow = tmp_path / "allowlist.txt"
    allow.write_text("elsewhere stack-protector\n")
    rc, _ = check([str(staging), str(allow)])
    assert rc == 0


@needs_gcc
def test_the_stack_protector_is_judged_per_package(tmp_path, empty):
    # A program with nothing to protect has no __stack_chk_fail and is not a finding when
    # another file of its package does.
    staging = _staging(tmp_path / "staging", {"pkg": GOOD})
    _gcc(staging / "pkg" / "rootfs" / "usr" / "bin" / "tiny", "int main(void){return 0;}\n", GOOD)
    rc, out = check([str(staging), str(empty)])
    assert rc == 0, out


@needs_gcc
def test_a_static_binary_is_not_held_to_dynamic_linking(tmp_path, empty):
    # No interpreter and no dynamic section: what a CGO_ENABLED=0 Go program is.
    staging = tmp_path / "staging"
    _gcc(
        staging / "static" / "rootfs" / "usr" / "bin" / "static",
        START_C,
        ["-nostdlib", "-static", "-no-pie", "-Wl,-z,noexecstack"],
    )
    rc, out = check([str(staging), str(empty)])
    assert rc == 0, out


@needs_gcc
def test_extensions_are_checked_too(tmp_path, empty):
    staging = tmp_path / "staging"
    _gcc(staging / "ext" / "extensions" / "debug" / "usr" / "bin" / "ext", APP_C, BROKEN["lazy"])
    rc, out = check([str(staging), str(empty)])
    assert rc == 1
    assert "ext: bind-now" in out
    assert "extensions/debug/usr/bin/ext" in out


def test_no_elf_at_all_is_an_error(tmp_path, empty):
    # A check that judged nothing must not pass: a wrong path is not a hardened tree.
    (tmp_path / "staging" / "data" / "rootfs").mkdir(parents=True)
    rc, out = check([str(tmp_path / "staging"), str(empty)])
    assert rc == 1
    assert "no ELF" in out


def test_a_missing_directory_is_an_error(tmp_path, empty):
    with pytest.raises(SystemExit):
        check([str(tmp_path / "nope"), str(empty)])


def test_the_allowlist_rejects_an_unknown_property(tmp_path):
    (tmp_path / "staging" / "p" / "rootfs").mkdir(parents=True)
    allow = tmp_path / "allowlist.txt"
    allow.write_text("p stack-protecter\n")
    with pytest.raises(SystemExit):
        check([str(tmp_path / "staging"), str(allow)])
