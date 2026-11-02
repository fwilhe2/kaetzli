"""kaetzli.qemu.machine against test/qemu-lib.sh, live.

The two harnesses are side by side until every boot test has moved, and
tools/boot-qemu.sh, on the bash one, must boot the machine CI booted: so each case here
runs the bash harness's qemu_setup, qemu_ssh_setup and qemu_argv with the same settings
and requires the same binary, argv and ssh_config back. Both read the host's /dev/kvm.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from kaetzli import repo
from kaetzli.qemu import console, machine
from kaetzli.qemu.machine import Machine, Refused, refuse

CMDLINE = (
    'root=/dev/dm-0 ro dm-mod.create="root,,,ro,0 8 verity 1 /dev/vda /dev/vda 4096 4096 '
    '1 1 sha256 ab cd" dm-mod.waitfor=/dev/vda rootfstype=erofs\n'
)


@pytest.fixture
def image(tmp_path):
    d = tmp_path / "img"
    d.mkdir()
    (d / "rootfs.erofs").write_bytes(b"\0" * 512)
    (d / "bzImage").write_bytes(b"\0")
    (d / "rootfs.cmdline").write_text(CMDLINE)
    for kind, name in (("extensions", "podman"), ("extensions", "debug"), ("confexts", "debug")):
        (d / kind).mkdir(exist_ok=True)
        (d / kind / f"{name}.raw").write_bytes(b"\0")
        (d / kind / f"{name}.dm-verity").write_text(
            f"0 16 verity 1 @DEV@ @DEV@ 4096 4096 2 2 sha256 {kind}{name} salt\n"
        )
    return d


def bash(env: dict[str, str], arrays: str = "", ssh: bool = False) -> tuple[list[str], int, str]:
    """The bash harness's binary + argv (NUL-separated), exit code and stderr."""
    script = "\n".join([
        "set -euo pipefail",
        "source test/qemu-lib.sh",
        arrays,
        "qemu_setup >/dev/null",
        "qemu_ssh_setup" if ssh else "",
        "qemu_preflight",
        "qemu_argv",
        'printf "%s\\0" "$QEMU" "${QEMU_ARGV[@]}"',
    ])  # fmt: skip
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("QEMU_", "SSH_"))}
    for k in ("ARCH", "MEM", "CPUS", "VAR_DISK", "SYSEXTS", "EXTENSIONS", "ROOT_CMDLINE"):
        clean.pop(k, None)
    p = subprocess.run(
        ["bash", "-c", script],
        cwd=repo.ROOT,
        env={**clean, **env},
        capture_output=True,
    )
    return p.stdout.decode().split("\0")[:-1], p.returncode, p.stderr.decode()


def python(env: dict[str, str], ssh: bool = False, **fields) -> list[str]:
    m = Machine.from_env(env, rootfs=env["ROOTFS"], kernel=env["KERNEL"], init=env["INIT"])
    for k, v in fields.items():
        setattr(m, k, v)
    m.setup()
    if ssh:
        m.ssh_setup(
            ssh_dir=env.get("SSH_DIR", "output/ssh"),
            port=int(env["SSH_PORT"]) if env.get("SSH_PORT") else None,
            proxy=env.get("SSH_PROXY", ""),
            keep_key=env.get("SSH_KEEP_KEY") == "1",
        )
    m.preflight()
    return [m.qemu, *m.argv()]


def base(image: Path, **extra: str) -> dict[str, str]:
    return {
        "ROOTFS": str(image / "rootfs.erofs"),
        "KERNEL": str(image / "bzImage"),
        "INIT": "/usr/lib/systemd/systemd",
        **extra,
    }


@pytest.fixture(autouse=True)
def qemu_on_path(tmp_path, monkeypatch):
    """preflight wants the binary; neither harness runs it here."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ("qemu-system-x86_64", "qemu-system-aarch64"):
        (bin_ / name).write_text("#!/bin/sh\nexit 0\n")
        (bin_ / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")


CASES = {
    "pc": {},
    "pc, sized": {"MEM": "4096", "CPUS": "4"},
    "pc, arm64": {"ARCH": "arm64"},
    "pc, aarch64 spelt so": {"ARCH": "aarch64"},
    "pc, reboot": {"QEMU_REBOOT": "1"},
    "pc, fw_cfg": {"QEMU_FWCFG_CREDENTIALS": "a=1 b=x=y c"},
    "pc, extensions": {"SYSEXTS": "podman debug"},
    "pc, extensions elsewhere": {"SYSEXTS": "debug", "EXTENSIONS": "{image}"},
    "pc, cmdline elsewhere": {"ROOT_CMDLINE": "{image}/rootfs.cmdline"},
    "microvm": {"QEMU_PROFILE": "microvm"},
    "microvm, arm64": {"QEMU_PROFILE": "microvm", "ARCH": "arm64"},
    "vz": {"QEMU_PROFILE": "vz"},
    "vz, virtiofs and vsock": {
        "QEMU_PROFILE": "vz",
        "VIRTIOFS_SOCKET": "/run/v.sock",
        "VSOCK_CID": "3",
    },
    "vz, arm64": {"QEMU_PROFILE": "vz", "ARCH": "arm64"},
}


@pytest.mark.parametrize("case", CASES)
def test_the_argv_is_the_bash_harnesses(case, image):
    env = base(image, **{k: v.format(image=image) for k, v in CASES[case].items()})
    want, rc, err = bash(env)
    assert rc == 0, err
    assert python(env) == want


def test_a_var_disk(image, tmp_path):
    disk = tmp_path / "state" / "var.img"
    env = base(image, VAR_DISK=str(disk), VAR_SIZE="64M", SYSEXTS="debug")
    want, rc, err = bash(env)
    assert rc == 0, err
    machine_id = (tmp_path / "state" / "var.img.machine-id").read_text()
    assert len(machine_id) == 32
    disk.unlink()  # made again by the Python, the same size; the id is kept
    assert python(env) == want
    assert disk.stat().st_size == 64 << 20
    assert (tmp_path / "state" / "var.img.machine-id").read_text() == machine_id


def test_a_var_disk_gets_a_machine_id_of_its_own(image, tmp_path):
    disk = tmp_path / "var.img"
    argv = python(base(image, VAR_DISK=str(disk)))
    machine_id = (tmp_path / "var.img.machine-id").read_text()
    assert int(machine_id, 16) and len(machine_id) == 32
    assert f"systemd.machine_id={machine_id}" in argv[argv.index("-append") + 1]


def test_arrays_replace_the_nic_and_the_display(image):
    env = base(image)
    want, rc, err = bash(
        env,
        arrays="QEMU_NIC_ARGS=(-netdev socket,id=n,mcast=230.0.0.1:1234 -device virtio-net-pci,"
        "netdev=n); QEMU_DISPLAY_ARGS=(-display none -serial unix:/tmp/c.sock,server=on)",
    )
    assert rc == 0, err
    nic = ["-netdev", "socket,id=n,mcast=230.0.0.1:1234", "-device", "virtio-net-pci,netdev=n"]
    display = ["-display", "none", "-serial", "unix:/tmp/c.sock,server=on"]
    assert python(env, nic_args=nic, display_args=display) == want


def test_an_empty_display_is_the_default(image):
    env = base(image)
    want, rc, err = bash(env, arrays="QEMU_DISPLAY_ARGS=()")
    assert rc == 0, err
    assert python(env, display_args=[]) == want


@pytest.mark.parametrize("proxy", ["", "socat - VSOCK-CONNECT:3:22"])
def test_ssh(image, tmp_path, proxy):
    keys = tmp_path / "ssh"
    keys.mkdir(mode=0o700)
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "t", "-f", keys / "id_ed25519"],
        check=True,
    )
    env = base(image, SSH_DIR=str(keys), SSH_KEEP_KEY="1", SSH_PORT="2345", SSH_PROXY=proxy)
    want, rc, err = bash(env, ssh=True)
    assert rc == 0, err
    config = (keys / "config").read_text()
    (keys / "config").unlink()
    assert python(env, ssh=True) == want
    assert (keys / "config").read_text() == config
    assert ("ProxyCommand" in config) == bool(proxy)


def test_ssh_finds_a_free_port_and_makes_a_key(image, tmp_path, monkeypatch):
    m = Machine.from_env(base(image), rootfs="r", kernel="k", init="i")
    tried = []
    monkeypatch.setattr(machine, "_listening", lambda port: tried.append(port) or port < 2224)
    port = m.ssh_setup(ssh_dir=str(tmp_path / "s"))
    assert port == 2224 and tried == [2222, 2223, 2224]
    assert (tmp_path / "s" / "id_ed25519").is_file()
    assert (tmp_path / "s").stat().st_mode & 0o777 == 0o700
    assert m.nic_extra == ",hostfwd=tcp:127.0.0.1:2224-:22"


REFUSALS = {
    "a profile": ({"QEMU_PROFILE": "firecracker"}, ""),
    "an arch": ({"ARCH": "riscv64"}, ""),
    "a kernel": ({"KERNEL": "{image}/nope"}, ""),
    "a rootfs": ({"ROOTFS": "{image}/nope.erofs"}, ""),
    "a cmdline": ({}, "rootfs.cmdline"),
    "an extension": ({"SYSEXTS": "nope"}, ""),
    "a verity table": ({"SYSEXTS": "podman"}, "extensions/podman.dm-verity"),
}


@pytest.mark.parametrize("case", REFUSALS)
def test_what_is_refused_says_the_same(case, image, capsys):
    settings, delete = REFUSALS[case]
    env = base(image, **{k: v.format(image=image) for k, v in settings.items()})
    if delete:
        (image / delete).unlink()
    _, rc, err = bash(env)
    assert rc == 1
    with pytest.raises(Refused) as e:
        python(env)
    assert refuse(e.value) == 1
    assert capsys.readouterr().err == err


def test_the_patterns_are_the_bash_harnesses():
    out = subprocess.run(
        ["bash", "-c", 'source test/qemu-lib.sh; printf "%s\\0" "$QEMU_DIED" "$CONSOLE_BAD" '
         '"$READY" "$PROBE" "$QUIET"'],
        cwd=repo.ROOT, capture_output=True, text=True, check=True,
    ).stdout.split("\0")[:-1]  # fmt: skip
    assert out == [
        console.QEMU_DIED,
        console.CONSOLE_BAD,
        console.READY,
        console.PROBE,
        console.QUIET,
    ]
