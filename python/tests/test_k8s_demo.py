"""tools/k8s-demo.py against the bash version it replaced: the router's and the nodes'
qemu command lines, the ssh_config the nodes are reached through and what booting them
prints, for two nodes. fixtures/k8s-demo/ is what tools/k8s-demo.sh's boot_router and
boot_node produced (up's first steps, but for `down`), through
fixtures/qemu/record_qemu.py, with the temporary directory, the ssh key and each disk's
random machine ID as placeholders. The cluster itself (kubeadm, flannel, the demo) needs
KVM and the network, and is test/k8s.py's, on CI's image."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kaetzli import k8s_demo, repo
from kaetzli.qemu import machine

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
GUESTS = ("k8s-router", "k8s-1", "k8s-2")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    img = tmp_path / "img"
    for d in ("extensions", "confexts"):
        (img / d).mkdir(parents=True)
        (img / d / "kubernetes.raw").write_bytes(b"\0")
        (img / d / "kubernetes.dm-verity").write_text(f"0 8 verity 1 @DEV@ @DEV@ {d}\n")
    (img / "rootfs.erofs").write_bytes(b"\0" * 512)
    (img / "bzImage").write_bytes(b"\0")
    (img / "rootfs.cmdline").write_text(
        'root=/dev/dm-0 ro dm-mod.create="root,,,ro,0 8 verity" dm-mod.waitfor=/dev/vda\n'
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("qemu-system-x86_64", "qemu-system-aarch64"):
        (bin_dir / name).write_text(
            f'#!/bin/sh\nexec {sys.executable} {FIXTURES}/qemu/record_qemu.py "$@"\n'
        )
        (bin_dir / name).chmod(0o755)
    state = tmp_path / "state"
    (state / "ssh").mkdir(parents=True)
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test",
         "-f", str(state / "ssh" / "id_ed25519")],
        check=True,
    )  # fmt: skip
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("QEMU_", "SSH_")) and k not in ("ARCH", "MEM", "CPUS", "SYSEXTS")
    }
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}", ROOTFS=str(img / "rootfs.erofs"),
        KERNEL=str(img / "bzImage"), NODES="2", SSH_PORT="2299", SSH_KEEP_KEY="1", ARCH="amd64",
    )  # fmt: skip
    for k in list(os.environ):
        if k.startswith(("QEMU_", "SSH_")) or k in ("ARCH", "MEM", "CPUS", "SYSEXTS"):
            monkeypatch.delenv(k)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(machine, "kvm_usable", lambda: True)
    return state, env


def normalize(data: bytes, tmp) -> bytes:
    """The temporary directory, the ssh key and each disk's random machine ID as
    placeholders, so that a recording compares."""
    data = data.replace(str(tmp).encode(), b"<tmp>")
    data = re.sub(rb"(ssh\.authorized_keys\.root:)[A-Za-z0-9+/=]+", rb"\1<key>", data)
    return re.sub(rb"(systemd\.machine_id=)[0-9a-f]{32}", rb"\1<machine-id>", data)


def collect(state) -> dict[str, bytes]:
    # The guests are started in the background: wait for what they record.
    for _ in range(100):
        if all((state / f"{g}.pid.argv").exists() for g in GUESTS):
            break
        time.sleep(0.1)
    time.sleep(0.2)
    got = {g: (state / f"{g}.pid.argv").read_bytes() for g in GUESTS}
    got["ssh_config"] = (state / "ssh_config").read_bytes()
    return got


def reset(state) -> None:
    """Back to before the boot, but for the key and each /var disk's machine ID, which the
    second run reuses as a second boot of the same disks would."""
    for p in state.iterdir():
        if p.name != "ssh" and not p.name.endswith((".machine-id", "-var.img")):
            shutil.rmtree(p) if p.is_dir() else p.unlink()


GOLDEN = os.path.join(FIXTURES, "k8s-demo")


def test_the_guests_are_the_bash_versions(setup, capfd, monkeypatch, tmp_path):
    state, _ = setup
    monkeypatch.chdir(repo.ROOT)
    monkeypatch.setattr(k8s_demo, "STATE", state)
    cluster = k8s_demo.Cluster.from_env(k8s_demo.base_machine())
    cluster.boot_router()
    cluster.boot_node(1)
    cluster.boot_node(2)
    got = {k: normalize(v, tmp_path) for k, v in collect(state).items()}
    got["stdout"] = normalize(capfd.readouterr().out.encode(), tmp_path)
    want = {k: Path(GOLDEN, k).read_bytes() for k in got}
    assert got == want


def test_down_returns_for_a_guest_this_process_started(tmp_path, monkeypatch):
    # test/k8s.py runs up and down in one process, so the qemus are its children: one
    # that has exited stays a zombie until reaped, and kill(pid, 0) still finds it.
    monkeypatch.setattr(k8s_demo, "STATE", tmp_path)
    guest = subprocess.Popen(["sleep", "60"])
    (tmp_path / "k8s-1.pid").write_text(f"{guest.pid}\n")
    deadline = time.monotonic() + 10
    monkeypatch.setattr(
        k8s_demo.time,
        "sleep",
        lambda s: time.monotonic() < deadline or pytest.fail("down waits on a zombie"),
    )
    k8s_demo.down()
    assert not tmp_path.exists()
