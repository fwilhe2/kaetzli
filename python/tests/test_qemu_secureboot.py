"""kaetzli.qemu.machine with SECUREBOOT: the signed UKI under OVMF with Secure Boot on,
its credentials by fw_cfg (the UKI's stub ignores a command line from outside)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kaetzli.qemu.machine import Machine, Refused


@pytest.fixture
def sb(tmp_path: Path) -> Path:
    d = tmp_path / "secureboot"
    d.mkdir()
    for f in ("kaetzli.efi", "ovmf-code.fd", "ovmf-vars.fd"):
        (d / f).write_bytes(b"x")
    (tmp_path / "rootfs.erofs").write_bytes(b"")
    (tmp_path / "rootfs.cmdline").write_text("root=/dev/dm-0 ro\n")
    return d


def machine(sb: Path, **kw) -> Machine:
    m = Machine(rootfs=str(sb.parent / "rootfs.erofs"), kernel="unused", init="/sbin/init",
                arch="amd64", kvm=True, secureboot=str(sb), **kw)  # fmt: skip
    m.setup()
    return m


def after(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


def test_the_kernel_is_the_signed_uki_under_secure_boot_firmware(sb):
    argv = machine(sb).argv()
    assert after(argv, "-kernel") == [str(sb / "kaetzli.efi")]
    assert "q35,smm=on" in after(argv, "-machine")
    assert "driver=cfi.pflash01,property=secure,value=on" in after(argv, "-global")
    drives = after(argv, "-drive")
    assert f"if=pflash,format=raw,unit=0,file={sb / 'ovmf-code.fd'},readonly=on" in drives
    assert any(d.startswith("if=pflash,format=raw,unit=1,file=") for d in drives)


def test_each_boot_writes_its_own_copy_of_the_variables(sb):
    m = machine(sb)
    vars_ = next(d for d in after(m.argv(), "-drive") if "unit=1" in d)
    path = Path(vars_.split("file=", 1)[1])
    assert path != sb / "ovmf-vars.fd" and path.read_bytes() == b"x"


def test_credentials_go_by_fw_cfg_since_the_command_line_is_sealed(sb, tmp_path):
    m = machine(sb)
    m.ssh_setup(str(tmp_path / "ssh"), port=2299)
    fw = after(m.argv(), "-fw_cfg")
    assert "name=opt/io.systemd.credentials/login.noauth,string=yes" in fw
    pub = tmp_path / "ssh" / "id_ed25519.pub"
    assert f"name=opt/io.systemd.credentials/ssh.authorized_keys.root,file={pub}" in fw


def test_a_var_disk_goes_by_credentials_not_the_sealed_command_line(sb, tmp_path):
    disk = tmp_path / "vm" / "var.img"
    argv = machine(sb, var_disk=str(disk)).argv()
    assert f"id=var,file={disk},format=raw,if=none" in after(argv, "-drive")
    fw = after(argv, "-fw_cfg")
    fstab = Path(f"{disk}.fstab")
    assert f"name=opt/io.systemd.credentials/fstab.extra,file={fstab}" in fw
    assert fstab.read_text() == "/dev/vdb /var ext4 x-systemd.makefs,x-systemd.growfs 0 0\n"
    mid = Path(f"{disk}.machine-id")
    assert f"name=opt/io.systemd.credentials/system.machine_id,file={mid}" in fw
    assert len(mid.read_text()) == 32
    # nothing for /var on the command line the stub ignores: Secure Boot off would mount it twice
    append = after(argv, "-append")[0]
    assert "/var" not in append and "machine_id" not in append


def test_the_var_disk_keeps_its_machine_id_across_boots(sb, tmp_path):
    disk = tmp_path / "var.img"
    machine(sb, var_disk=str(disk)).argv()
    first = Path(f"{disk}.machine-id").read_text()
    machine(sb, var_disk=str(disk)).argv()
    assert Path(f"{disk}.machine-id").read_text() == first


@pytest.mark.parametrize(
    ("kw", "says"),
    [
        ({"profile": "microvm"}, "QEMU_PROFILE"),
    ],
)
def test_what_secure_boot_cannot_do_yet_is_refused(sb, kw, says):
    with pytest.raises(Refused, match=says):
        machine(sb, **kw)


def test_arm64_is_refused(sb):
    m = Machine(rootfs="r", kernel="k", init="i", arch="arm64", kvm=True, secureboot=str(sb))
    with pytest.raises(Refused, match="amd64"):
        m.setup()


def test_preflight_names_a_missing_file(sb, monkeypatch):
    # qemu is checked first, as in test/qemu-lib.sh; CI's python job has none.
    monkeypatch.setattr("kaetzli.qemu.machine.shutil.which", lambda name: f"/usr/bin/{name}")
    (sb / "ovmf-vars.fd").unlink()
    with pytest.raises(Refused, match=r"ovmf-vars\.fd.*sign-uki"):
        machine(sb).preflight()


def test_from_env_reads_secureboot(sb):
    m = Machine.from_env({"SECUREBOOT": str(sb)}, rootfs="r", kernel="k", init="i")
    assert m.secureboot == str(sb)


def test_another_variable_store_can_be_booted(sb):
    (sb / "ovmf-vars-off.fd").write_bytes(b"off")
    m = machine(sb, secureboot_vars="ovmf-vars-off.fd")
    vars_ = next(d for d in after(m.argv(), "-drive") if "unit=1" in d)
    assert Path(vars_.split("file=", 1)[1]).read_bytes() == b"off"


def sealed(sb: Path, exts: str, var: bool) -> None:
    (sb / "sealed").write_text(f"extensions={exts}\nvar={'true' if var else 'false'}\n")


def extension(sb: Path, name: str = "podman") -> Path:
    d = sb.parent / "extensions"
    d.mkdir(exist_ok=True)
    (d / f"{name}.raw").write_bytes(b"")
    (d / f"{name}.dm-verity").write_text("0 8 verity 1 @DEV@ @DEV@\n")
    return d


# The UKI's command line carries the extensions' tables (tools/sign-uki.sh --ext), with
# disk names that depend on whether a /var disk comes first: a boot must match it.
def test_extensions_boot_when_the_uki_was_sealed_for_them(sb, tmp_path):
    extension(sb)
    sealed(sb, "podman", var=True)
    m = machine(sb, sysexts=["podman"], var_disk=str(tmp_path / "var.img"))
    drives = after(m.argv(), "-drive")
    assert any(d.endswith(",id=ext-podman") for d in drives)


@pytest.mark.parametrize(
    ("exts", "var", "kw", "says"),
    [
        ("", False, {"sysexts": ["podman"]}, "sealed for no extensions"),
        ("debug", False, {"sysexts": ["podman"]}, "sealed for debug"),
        ("podman", False, {"sysexts": ["podman"], "var_disk": "/tmp/v.img"}, "without a /var disk"),
        ("podman", True, {"sysexts": ["podman"]}, "with a /var disk"),
        ("podman", False, {}, "sealed for podman"),
    ],
)
def test_a_boot_the_uki_was_not_sealed_for_is_refused(sb, exts, var, kw, says):
    extension(sb)
    sealed(sb, exts, var)
    with pytest.raises(Refused, match=says):
        machine(sb, **kw)


def test_without_a_sealed_file_extensions_are_refused(sb):
    extension(sb)
    with pytest.raises(Refused, match=r"sign-uki\.sh --ext"):
        machine(sb, sysexts=["podman"])
