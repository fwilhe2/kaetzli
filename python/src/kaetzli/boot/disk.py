"""test/disk.py: boot a disk from tools/build-disk.sh the way a cloud boots an instance:
UEFI firmware, the disk as the only drive, no -kernel and no command line from outside,
and an instance UUID in SMBIOS.

    ./test/disk.py [disk.qcow2]          # default output/kaetzli-disk-<arch>.qcow2

Overrides, from the environment: DISK_BUS (virtio or scsi, as the disk was built with
--bus; default virtio), TIMEOUT, MEM/CPUS, LOG (default output/disk-test.log). amd64 for
now: the firmware is OVMF from the pinned assembler image (tools/prep.sh assembler),
Secure Boot off.

What the disk was built for is read back from the guest's /proc/cmdline, which is the
UKI's own, so one test covers every option:

  always                 the system is running, no unit failed; the firmware started
                         the UKI (EFI variables exist); / is the
                         verity device, on partition 2 of the bus's disk
                         (/dev/vda2 or /dev/sda2); the machine ID is the SMBIOS UUID
                         (systemd.machine_id=firmware)
  a /var partition       /var is ext4 on partition 3, made by systemd-makefs
  none (--var-size 0)    /var is tmpfs
  --ssh-key              the ssh.authorized_keys.root credential arrived

The disk is opened with snapshot=on, so the file is not changed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from kaetzli import repo
from kaetzli.boot import run
from kaetzli.qemu import Guest, Refused, machine

BUSES = {"virtio": "/dev/vda", "scsi": "/dev/sda"}
# The instance's SMBIOS UUID, as a cloud sets one; the machine ID must be it.
UUID = "6c1d7f0e-5b55-4b0a-9a0d-6a1f3c2b9e41"


def check(dev: str) -> str:
    return (
        "c=$(</proc/cmdline); "
        "[[ $(systemctl is-system-running --wait) == running ]] && "
        "[ -d /sys/firmware/efi/efivars ] && "
        f"[[ $c == *'verity 1 {dev}2 {dev}2'* ]] && "
        "[[ $(findmnt -no SOURCE /) == @(/dev/dm-0|/dev/mapper/root) ]] && "
        "if [[ $c == *systemd.mount-extra=* ]]; then "
        f"[[ $(findmnt -rno SOURCE,FSTYPE /var) == '{dev}3 ext4' ]]; "
        "else [[ $(findmnt -rno FSTYPE /var) == tmpfs ]]; fi && "
        f"[[ $(</etc/machine-id) == {UUID.replace('-', '')} ]] && "
        "if [[ $c == *ssh.authorized_keys.root:* ]]; then "
        "[ -s /run/credentials/@system/ssh.authorized_keys.root ]; fi && "
        "echo DISK'-OK' $([[ $c == *mount-extra* ]] && echo var-partition || echo var-tmpfs)"
        "$([[ $c == *ssh.authorized_keys.root:* ]] && echo ' ssh-key') DISK'-END'"
    )


DIAGNOSE = (
    "cat /proc/cmdline; findmnt / /var; cat /etc/machine-id /sys/class/dmi/id/product_uuid; "
    "systemctl --no-pager --failed; "
    "systemctl --no-pager status var.mount; "
    "journalctl --no-pager -b -u 'systemd-makefs*' -n 40"
)


def firmware(into: Path) -> tuple[Path, Path]:
    """OVMF's code and a copy of its variable store, from the assembler image."""
    assembler = subprocess.run(
        ["./tools/prep.sh", "assembler"], check=True, capture_output=True, text=True
    ).stdout.split()[-1]
    subprocess.run(
        [
            "podman", "run", "--rm", "--network=none", "--security-opt", "label=disable",
            "-v", f"{into}:/out", assembler, "sh", "-euc",
            "cp /usr/share/OVMF/OVMF_CODE_4M.fd /out/code.fd; "
            "cp /usr/share/OVMF/OVMF_VARS_4M.fd /out/vars.fd",
        ],
        check=True,
    )  # fmt: skip
    return into / "code.fd", into / "vars.fd"


def drive(disk: str, bus: str) -> list[str]:
    fmt = "qcow2" if disk.endswith(".qcow2") else "raw"
    spec = f"file={disk},format={fmt},if=none,id=disk0,snapshot=on"
    if bus == "scsi":
        return ["-drive", spec, "-device", "virtio-scsi-pci", "-device", "scsi-hd,drive=disk0"]
    return ["-drive", spec, "-device", "virtio-blk-pci,drive=disk0"]


def argv(disk: str, bus: str, code: Path, vars_fd: Path, kvm: bool) -> list[str]:
    accel = ["-enable-kvm", "-cpu", "host"] if kvm else ["-cpu", "max"]
    return [
        *accel,
        "-machine", "q35",
        "-m", os.environ.get("MEM") or "1024", "-smp", os.environ.get("CPUS") or "2",
        "-drive", f"if=pflash,format=raw,unit=0,file={code},readonly=on",
        "-drive", f"if=pflash,format=raw,unit=1,file={vars_fd}",
        *drive(disk, bus),
        "-uuid", UUID,
        "-nic", "user,model=virtio-net-pci",
        "-fw_cfg", "name=opt/io.systemd.credentials/login.noauth,string=yes",
        "-nographic", "-no-reboot",
    ]  # fmt: skip


def main(argv_: list[str]) -> int:
    repo.enter_root()
    arch = repo.normalize_arch(os.environ.get("ARCH") or None)
    disk = argv_[0] if argv_ else f"output/kaetzli-disk-{arch}.qcow2"
    bus = os.environ.get("DISK_BUS") or "virtio"
    log = os.environ.get("LOG") or "output/disk-test.log"
    timeout = float(os.environ.get("TIMEOUT") or "300")
    work = Path(tempfile.mkdtemp())

    def test() -> None:
        if arch != "amd64":
            raise Refused("test/disk.py is amd64 only for now")
        if bus not in BUSES:
            raise Refused(f"DISK_BUS={bus} (expected virtio or scsi)")
        if not os.path.isfile(disk):
            raise Refused(f"no {disk} (./tools/build-disk.sh makes it)")
        kvm = machine.kvm_usable()
        if not kvm:
            print("note: /dev/kvm not available, emulating (slow)", flush=True)
        code, vars_fd = firmware(work)
        print(f">> booting {disk} ({bus}) under OVMF, waiting up to {timeout:g}s", flush=True)
        args = argv(disk, bus, code, vars_fd, kvm)
        with Guest("qemu-system-x86_64", args, log, timeout, name="disk",
                   diagnose=DIAGNOSE) as guest:  # fmt: skip
            guest.login()
            guest.send(check(BUSES[bus]))
            if not guest.wait(r"DISK-OK ([a-z -]*) DISK-END", 180):
                guest.fail(
                    "a unit failed, or the disk's command line, root, /var or machine ID "
                    "is not what tools/build-disk.sh made it for"
                )
            print(
                f">> disk OK ({bus}, {guest.group().strip()}): booted by UEFI from the ESP, "
                f"root verified on {BUSES[bus]}2, machine ID from SMBIOS",
                flush=True,
            )

    try:
        return run(test)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
