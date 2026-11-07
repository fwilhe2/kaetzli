"""The Python boot tests against the bash ones they replaced.

Each fixtures/<test>/<case>/ is a guest, as fixtures/qemu/fake_guest.py plays it from
responses.json (and with ssh.json the host's ssh client, as fixtures/qemu/fake_ssh.py
answers): a clean boot, and one broken in each way the test looks for; `timeout`
and `env` (KEY=VALUE lines), when there, are its TIMEOUT and more of its environment.
expected/ is what test/<test>.sh printed and returned against that same fake qemu (at
its last commit, with <tmp> for the temporary directory and <machine-id> for a random
machine ID), console tail and all; the
Python has to reproduce it byte for byte.
Deliberate differences are edited into expected/: listeners/extra names the Python
module, where the expected list now lives, rather than the bash script; systemd/ok and
systemd/exposure-* end with the services' exposure, which the bash test never checked, and
the two that pass with the boot's time.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from kaetzli.boot import (
    container,
    listeners,
    network,
    persistent_var,
    podman,
    rootless,
    ssh,
    sysext,
    systemd,
)
from kaetzli.qemu import console, machine

TESTS = {
    "systemd": systemd,
    "network": network,
    "listeners": listeners,
    "ssh": ssh,
    "container": container,
    "podman": podman,
    "rootless": rootless,
    "persistent-var": persistent_var,
    "sysext": sysext,
}
FIXTURES = Path(__file__).parent / "fixtures"
FAKE = FIXTURES / "qemu" / "fake_guest.py"
FAKE_SSH = FIXTURES / "qemu" / "fake_ssh.py"
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
    for name in ("kubernetes", "podman"):
        (tmp_path / "img" / "extensions" / f"{name}.raw").touch()
        (tmp_path / "img" / "extensions" / f"{name}.dm-verity").write_text(
            "0 8 verity 1 @DEV@ @DEV@\n"
        )
    (tmp_path / "bin").mkdir()
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("LOG", str(tmp_path / "console.log"))
    for name in ("ARCH", "QEMU_PROFILE", "LOGIN_USER", "INIT", "MEM", "CPUS", "SYSEXTS",
                 "HOST", "PORT", "ROUNDS", "EXTENSIONS", "SSH_PORT", "PREBUILT"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(machine, "kvm_usable", lambda: True)  # no note either way
    monkeypatch.setattr(console, "DIAGNOSE_WAIT", 0.5)
    monkeypatch.setattr(console, "SETTLE", 0)
    monkeypatch.chdir(tmp_path)  # main() enters the checkout; this puts it back
    return tmp_path


def fake_extensions(image: Path, monkeypatch) -> None:
    """test/sysext.py builds its toys with tools/build-extension.sh and seeds a /var disk
    with mkfs.ext4, neither of which a unit test can run; its golden was recorded with
    the same stand-ins, an image of zeroes and a table, in a worktree."""
    out = image / "output"

    def build_extension(kind: str, name: str, tree: Path, level: str = "") -> None:
        assert tree.is_dir()
        d = out / ("extensions" if kind == "sysext" else "confexts")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.raw").write_bytes(bytes(4096))
        (d / f"{name}.dm-verity").write_text("0 8 verity 1 @DEV@ @DEV@\n")

    monkeypatch.setattr(sysext, "OUT", str(out))
    monkeypatch.setattr(sysext, "build_extension", build_extension)
    monkeypatch.setattr(machine, "seed_var_disk", lambda disk, size, tree: Path(disk).touch())


def normalize(text: str, image: Path) -> str:
    """The temporary directory as <tmp>, and a machine ID (random per run) as <machine-id>."""
    return re.sub(r"\b[0-9a-f]{32}\b", "<machine-id>", text.replace(str(image), "<tmp>"))


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
        f'#!/bin/sh\nexec {sys.executable} {FAKE} systemd {fixture / "responses.json"} "$@"\n'
    )
    qemu.chmod(0o755)
    if (fixture / "ssh.json").exists():
        fake_ssh = image / "bin" / "ssh"
        fake_ssh.write_text(
            f'#!/bin/sh\nexec {sys.executable} {FAKE_SSH} {fixture / "ssh.json"} "$@"\n'
        )
        fake_ssh.chmod(0o755)
    if test == "sysext":
        fake_extensions(image, monkeypatch)
    rc = TESTS[test].main([str(image / "img" / "rootfs.erofs"), str(image / "img" / "bzImage")])
    out, err = capfd.readouterr()
    expected = fixture / "expected"
    assert (
        normalize(out, image),
        normalize(err, image),
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
