"""The library checks: test/check-rootfs-deps.py, check-symbol-versions.py,
check-symbol-resolution.py.

The trees are compiled by elf_trees.py, and fixtures/elf/expected/ is what the bash
versions printed for each case in elf_trees.CASES (exits.txt their exit codes), with the
tree's path as ROOT. One deliberate difference is edited into it: the note about skipped
objects names check-rootfs-deps.py. test/selftest.sh runs the same checks on its own trees.
"""

import io
import os
import struct
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

import elf_trees
import pytest

from kaetzli.elf import deps, resolution, scan, versions

EXPECTED = Path(__file__).parent / "fixtures" / "elf" / "expected"
EXITS = dict(line.split() for line in (EXPECTED / "exits.txt").read_text().splitlines() if line)
MODULES = {"deps": deps, "versions": versions, "resolution": resolution}

needs_gcc = pytest.mark.skipif(not elf_trees.available(), reason="needs gcc and readelf")


@pytest.fixture(scope="module")
def trees(tmp_path_factory):
    if not elf_trees.available():
        pytest.skip("needs gcc and readelf")
    return elf_trees.build(tmp_path_factory.mktemp("elf"))


def check(module, argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = module.main(argv)
    return rc, buf.getvalue()


@needs_gcc
@pytest.mark.parametrize(
    ("name", "kind", "tree", "allow"), elf_trees.CASES, ids=[c[0] for c in elf_trees.CASES]
)
def test_the_bash_versions_output(trees, tmp_path, name, kind, tree, allow):
    argv = [str(trees[tree])]
    if allow is not None:
        a = tmp_path / "allowlist.txt"
        a.write_text("".join(x + "\n" for x in allow))
        argv.append(str(a))
    rc, out = check(MODULES[kind], argv)
    assert out.replace(str(trees[tree]), "ROOT") == (EXPECTED / f"{name}.out").read_text()
    assert rc == int(EXITS[name])


@pytest.mark.parametrize("module", [deps, versions, resolution])
def test_a_missing_tree_is_an_error(module, tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        module.main([str(tmp_path / "none")])
    assert e.value.code == 1
    assert f"error: no such directory: {tmp_path}/none" in capsys.readouterr().err


@pytest.mark.parametrize("module", [deps, versions, resolution])
def test_a_broken_elf_fails_the_check_and_is_named(module, tmp_path):
    # Truncated: what a check would otherwise pass without having read a byte of it.
    (tmp_path / "usr" / "bin").mkdir(parents=True)
    (tmp_path / "usr" / "bin" / "truncated").write_bytes(b"\x7fELF\x02\x01\x01" + b"\0" * 40)
    rc, out = check(module, [str(tmp_path)])
    assert rc == 1
    assert "usr/bin/truncated" in out


@needs_gcc
class TestScan:
    def test_what_a_library_says(self, trees):
        e = scan.read(trees["good"], "usr/lib/libfoo.so.1", symbols=True)
        assert e.soname == "libfoo.so.1"
        assert e.defines == ["libfoo.so.1", "FOO_1", "FOO_2"]
        assert {"foo_old", "foo_new", "foo_v1", "foo_v2"} <= e.defined
        assert e.has_dynsym

    def test_what_a_program_says(self, trees):
        e = scan.read(trees["good"], "usr/bin/app", symbols=True)
        assert e.soname is None
        assert e.name == "app"  # no SONAME: its file name
        assert "libfoo.so.1" in e.needed
        assert ("libfoo.so.1", "FOO_2") in e.needs
        assert {"foo_old", "foo_new", "foo_v1", "foo_v2"} <= set(e.undefined)
        assert "foo_maybe" not in e.undefined  # weak

    def test_symbols_are_read_only_when_asked(self, trees):
        e = scan.read(trees["good"], "usr/lib/libfoo.so.1")
        assert not e.has_dynsym
        assert e.defined == frozenset()

    def test_the_tree_in_parallel_is_the_tree_in_order(self, trees):
        one = scan.scan(trees["many"], symbols=True, jobs=1)
        assert [e.path for e in one] == sorted(e.path for e in one)
        many = scan.scan(trees["many"], symbols=True, jobs=4)
        assert many == one


class TestScanFiles:
    def test_not_elf_is_skipped(self, tmp_path):
        (tmp_path / "script").write_text("#!/bin/sh\n")
        (tmp_path / "empty").write_bytes(b"")
        assert scan.read(tmp_path, "script") is None
        assert scan.read(tmp_path, "empty") is None

    def test_a_broken_elf_is_an_error(self, tmp_path):
        # The bash versions sent readelf's errors to /dev/null, so a file that starts like
        # ELF and cannot be read was skipped, and a check that skips passes. Not kept.
        (tmp_path / "broken").write_bytes(b"\x7fELF" + struct.pack("<B", 9) + b"\0" * 8)
        with pytest.raises(scan.Unreadable) as e:
            scan.read(tmp_path, "broken")
        assert [rel for rel, _ in e.value.files] == ["broken"]

    def test_every_broken_elf_is_named(self, tmp_path):
        for name in ("a", "b"):
            (tmp_path / name).write_bytes(b"\x7fELF" + b"\0" * 12)
        (tmp_path / "script").write_text("#!/bin/sh\n")
        with pytest.raises(scan.Unreadable) as e:
            scan.scan(tmp_path, jobs=1)
        assert [rel for rel, _ in e.value.files] == ["a", "b"]

    def test_regular_files_leave_links_out(self, tmp_path):
        (tmp_path / "d").mkdir()
        (tmp_path / "d" / "f").write_text("")
        (tmp_path / "d" / "l").symlink_to("f")
        (tmp_path / "dl").symlink_to("d")
        os.mkfifo(tmp_path / "fifo")
        assert scan.regular_files(tmp_path) == ["d/f"]
        assert scan.symlinks(tmp_path) == ["d/l", "dl"]


@needs_gcc
class TestDeps:
    # The allowlist is per library, so a line whose consumer is gone would wave through
    # the next one: each run names what it did not need. Not a failure, since CI runs the
    # check per tree (the base, each extension over it, the OCI layer) and one tree's
    # unused line can be another's backlog.
    @pytest.mark.parametrize(
        ("tree", "unused"),
        [("nolib", ["libgone.so.9"]), ("good", ["libfoo.so.1", "libgone.so.9"])],
    )
    def test_an_allowlisted_library_nothing_needs_is_named(self, trees, tmp_path, tree, unused):
        a = tmp_path / "allowlist.txt"
        a.write_text("# backlog\nlibfoo.so.1\nlibgone.so.9\n")
        rc, out = check(deps, [str(trees[tree]), str(a)])
        assert rc == 0
        note = out.split("needed by nothing in", 1)[1].split("\n\n", 1)[0].splitlines()[1:]
        assert [x.strip() for x in note] == unused
        satisfied = f"All shared library dependencies in {trees[tree]}/ are satisfied."
        assert out.splitlines()[-1] in ("No new unresolved dependencies.", satisfied)

    def test_a_link_is_read_inside_the_tree(self, trees, tmp_path):
        # An absolute link names the tree's own path, not the host's: libfoo.so.1 is
        # provided here by a link whose name is not one, to a target that is.
        t = tmp_path / "t"
        subprocess.run(["cp", "-a", str(trees["nolib"]), str(t)], check=True)
        lib = t / "usr" / "lib"
        (lib / "real").mkdir()
        subprocess.run(
            ["cp", str(trees["good"] / "usr/lib/libfoo.so.1"), str(lib / "real/foo")],
            check=True,
        )
        (lib / "foo.so").symlink_to("/usr/lib/real/foo")
        assert deps.provided(t) >= {"foo.so", "libfoo.so.1"}
        assert check(deps, [str(t), os.devnull])[0] == 0


@needs_gcc
class TestResolution:
    def test_an_allowlisted_symbol_is_accepted(self, trees, tmp_path):
        allow = tmp_path / "allow.txt"
        allow.write_text("# a plugin\napp foo_new\napp foo_v2   # comment\nlibx.so a b\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert resolution.check(trees["old"], str(trees["old"]), allow) == 0

    def test_the_allowlist_is_per_object(self, trees, tmp_path):
        allow = tmp_path / "allow.txt"
        allow.write_text("app foo_new\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert resolution.check(trees["old"], str(trees["old"]), allow) == 1
        assert "foo_v2" in buf.getvalue()
        assert "foo_new" not in buf.getvalue()
