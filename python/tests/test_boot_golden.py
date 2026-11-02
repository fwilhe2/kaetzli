"""The Python boot tests against the bash ones they replaced.

Each fixtures/<test>/<case>/ is a guest, as fixtures/qemu/fake_guest.py plays it from
responses.json: a clean boot, and one broken in each way the test looks for; `timeout`
and `env` (KEY=VALUE lines), when there, are its TIMEOUT and more of its environment.
expected/ is what test/<test>.sh printed and returned against that same fake qemu (at
its last commit, with <tmp> for the temporary directory), console tail and all; the
Python has to reproduce it byte for byte.
Deliberate differences are edited into expected/: listeners/extra names the Python
module, where the expected list now lives, rather than the bash script.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kaetzli.boot import listeners, network, systemd
from kaetzli.qemu import console, machine

TESTS = {"systemd": systemd, "network": network, "listeners": listeners}
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
    (tmp_path / "img" / "rootfs.cmdline").write_text(
        'root=/dev/dm-0 ro dm-mod.create="root,,,ro,0 8 verity" dm-mod.waitfor=/dev/vda\n'
    )
    (tmp_path / "img" / "extensions").mkdir()
    (tmp_path / "img" / "extensions" / "kubernetes.raw").touch()
    (tmp_path / "img" / "extensions" / "kubernetes.dm-verity").write_text(
        "0 8 verity 1 @DEV@ @DEV@\n"
    )
    (tmp_path / "bin").mkdir()
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("LOG", str(tmp_path / "console.log"))
    for name in ("ARCH", "QEMU_PROFILE", "LOGIN_USER", "INIT", "MEM", "CPUS", "SYSEXTS",
                 "HOST", "PORT", "ROUNDS", "EXTENSIONS"):  # fmt: skip
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
    if (fixture / "env").exists():
        for line in (fixture / "env").read_text().splitlines():
            monkeypatch.setenv(*line.split("=", 1))
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
