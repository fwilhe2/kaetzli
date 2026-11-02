"""The Python boot tests against the bash ones they replaced.

Each fixtures/<test>/<case>/ is a guest, as fixtures/qemu/fake_guest.py plays it from
responses.json: a clean boot, and one broken in each way the test looks for; `timeout`,
when there, is its TIMEOUT. expected/ is what test/<test>.sh printed and returned
against that same fake qemu (at its last commit, with <tmp> for the temporary
directory), console tail and all; the Python has to reproduce it byte for byte.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kaetzli.boot import network, systemd
from kaetzli.qemu import console, machine

TESTS = {"systemd": systemd, "network": network}
FIXTURES = Path(__file__).parent / "fixtures"
FAKE = FIXTURES / "qemu" / "fake_guest.py"
CASES = [
    f"{test}/{case.name}"
    for test in TESTS
    for case in sorted((FIXTURES / test).iterdir())
    if case.is_dir()
]


@pytest.fixture
def image(tmp_path, monkeypatch):
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "rootfs.erofs").touch()
    (tmp_path / "img" / "bzImage").touch()
    (tmp_path / "img" / "rootfs.cmdline").write_text("root=/dev/dm-0 ro\n")
    (tmp_path / "bin").mkdir()
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("LOG", str(tmp_path / "console.log"))
    for name in ("ARCH", "QEMU_PROFILE", "LOGIN_USER", "INIT", "MEM", "CPUS", "SYSEXTS",
                 "HOST", "PORT"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(machine, "kvm_usable", lambda: True)  # no note either way
    monkeypatch.setattr(console, "DIAGNOSE_WAIT", 0.5)
    monkeypatch.setattr(console, "SETTLE", 0)
    monkeypatch.chdir(tmp_path)  # main() enters the checkout; this puts it back
    return tmp_path


@pytest.mark.parametrize("case", CASES)
def test_the_output_is_the_bash_versions(case, image, capfd, monkeypatch):
    test, _ = case.split("/")
    fixture = FIXTURES / case
    timeout = fixture / "timeout"
    monkeypatch.setenv("TIMEOUT", timeout.read_text().strip() if timeout.exists() else "30")
    qemu = image / "bin" / "qemu-system-x86_64"
    qemu.write_text(
        f"#!/bin/sh\nexec {sys.executable} {FAKE} systemd {fixture / 'responses.json'}\n"
    )
    qemu.chmod(0o755)
    rc = TESTS[test].main([str(image / "img" / "rootfs.erofs"), str(image / "img" / "bzImage")])
    out, err = capfd.readouterr()
    expected = fixture / "expected"
    assert (
        out.replace(str(image), "<tmp>"),
        err.replace(str(image), "<tmp>"),
        rc,
    ) == (
        (expected / "stdout").read_bytes().decode(),  # not read_text: it eats \r
        (expected / "stderr").read_bytes().decode(),
        int((expected / "rc").read_text()),
    )


def test_unknown_errors_are_greps(tmp_path):
    known = tmp_path / "known"
    known.write_text("# why\n\n  # indented comment\nfoo\\[[0-9]+\\]: [[:alpha:]]+ broke\n")
    lines = ["foo[12]: something broke", "foo[x]: something broke", "bar: broke"]
    assert systemd.unknown_errors(lines, str(known)) == lines[1:]
    known.write_text("# nothing accepted\n")
    assert systemd.unknown_errors(lines, str(known)) == lines
