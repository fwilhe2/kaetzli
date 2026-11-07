"""tools/boot-qemu.py against the bash version it replaced.

Each fixtures/boot-qemu/<case>/ is an environment (env, with <tmp> for the temporary
directory) and qemu's extra arguments (args); expected/ is what tools/boot-qemu.sh gave a
stand-in qemu (fixtures/boot-qemu/qemu) for them: the argv, the ssh_config the guest was
reachable by, stdout, stderr and the exit code, with the key as <key>. Both harnesses read
the host's /dev/kvm, so the recording had KVM and the test says it does.

One case is not the bash version's: `proxy`, where it died on an unset SSH_PORT and left
the key behind; the Python prints the proxy instead.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

from kaetzli.qemu import interactive, machine

FIXTURES = Path(__file__).parent / "fixtures" / "boot-qemu"
CASES = sorted(d.name for d in FIXTURES.iterdir() if (d / "env").is_file())


def normalize(text: str, tmp: Path) -> str:
    text = text.replace(str(tmp), "<tmp>")
    return re.sub(
        r"ssh\.authorized_keys\.root:[A-Za-z0-9+/=]*", "ssh.authorized_keys.root:<key>", text
    )


@pytest.mark.parametrize("case", CASES)
def test_the_machine_is_the_bash_versions(case, tmp_path, monkeypatch, capfd):
    fixture = FIXTURES / case
    shutil.copytree(FIXTURES / "img", tmp_path / "img")
    (tmp_path / "bin").mkdir()
    (tmp_path / "record").mkdir()
    for name in ("qemu-system-x86_64", "qemu-system-aarch64"):
        (tmp_path / "bin" / name).symlink_to(FIXTURES / "qemu")
    (tmp_path / "var.img.machine-id").write_text("0123456789abcdef0123456789abcdef\n")
    env = {"PATH": f"{tmp_path}/bin:{os.environ['PATH']}", "RECORD": str(tmp_path / "record")}
    for line in (fixture / "env").read_text().splitlines():
        key, _, value = line.partition("=")
        env[key] = value.replace("<tmp>", str(tmp_path))
    args = (fixture / "args").read_text().splitlines() if (fixture / "args").is_file() else []
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(machine, "kvm_usable", lambda path="/dev/kvm": True)
    monkeypatch.setattr(interactive.signal, "signal", lambda *_: None)

    rc = interactive.main(args, env=env)

    out, err = capfd.readouterr()
    expected = fixture / "expected"
    assert (normalize(out, tmp_path), normalize(err, tmp_path), rc) == (
        (expected / "stdout").read_text(),
        (expected / "stderr").read_text(),
        int((expected / "rc").read_text()),
    )
    for name in ("argv", "ssh_config"):
        recorded = tmp_path / "record" / name
        want = expected / name
        assert recorded.is_file() == want.is_file(), name
        if want.is_file():
            got = recorded.read_text()
            if name == "argv":
                got = "".join(f"{a}\n" for a in got.split("\0")[:-1])
            assert normalize(got, tmp_path) == want.read_text()
    assert not (tmp_path / "ssh").exists(), "the ephemeral key outlived qemu"
