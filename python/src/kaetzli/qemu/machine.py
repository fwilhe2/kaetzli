"""The machine a boot test starts: test/qemu-lib.sh's qemu_setup, qemu_preflight,
qemu_ssh_setup, qemu_sysexts, qemu_var_disk and qemu_argv, as one dataclass.

Until every boot test has moved, the bash harness and this one are side by side, and
python/tests/test_qemu_machine.py holds them to the same command line for the same
settings: tools/boot-qemu.sh, which still sources the bash one, has to boot the machine
CI booted.

Settings come from the environment, under the names the bash harness reads, through
`Machine.from_env`; what bash held in arrays (QEMU_NIC_ARGS, QEMU_DISPLAY_ARGS) are lists
here, set by the caller.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from kaetzli import repo

PROFILES = ("pc", "microvm", "vz")
_LETTERS = "bcdefghijklmnopqrstuvwxyz"


class Refused(Exception):
    """A machine that cannot start: the message is the bash harness's error line(s)."""


def kvm_usable(path: str = "/dev/kvm") -> bool:
    return os.access(path, os.W_OK)


@dataclass
class Machine:
    rootfs: str
    kernel: str
    init: str
    arch: str = ""
    profile: str = "pc"
    mem: str = "1024"
    cpus: str = "2"
    root_cmdline: str = ""  # default: rootfs.cmdline beside the image
    var_disk: str = ""
    var_size: str = "4G"
    sysexts: list[str] = field(default_factory=list)
    extensions: str = ""  # default: the image's directory
    fwcfg_credentials: list[str] = field(default_factory=list)
    nic_args: list[str] | None = None  # replaces pc's user-mode NIC
    display_args: list[str] | None = None  # replaces pc's -nographic, when not empty
    reboot: bool = False
    virtiofs_socket: str = ""
    vsock_cid: str = ""
    hint: str = ""  # names the command that would make a missing file
    kvm: bool | None = None  # default: whether /dev/kvm is writable
    # What qemu_ssh_setup adds.
    nic_extra: str = ""
    append_extra: str = ""
    ssh_dir: str = ""

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, rootfs: str, kernel: str, init: str
    ) -> Machine:
        env = os.environ if env is None else env
        return cls(
            rootfs=rootfs,
            kernel=kernel,
            init=init,
            arch=env.get("ARCH", ""),
            profile=env.get("QEMU_PROFILE") or "pc",
            mem=env.get("MEM") or "1024",
            cpus=env.get("CPUS") or "2",
            root_cmdline=env.get("ROOT_CMDLINE", ""),
            var_disk=env.get("VAR_DISK", ""),
            var_size=env.get("VAR_SIZE") or "4G",
            sysexts=env.get("SYSEXTS", "").split(),
            extensions=env.get("EXTENSIONS", ""),
            fwcfg_credentials=env.get("QEMU_FWCFG_CREDENTIALS", "").split(),
            reboot=env.get("QEMU_REBOOT", "0") == "1",
            virtiofs_socket=env.get("VIRTIOFS_SOCKET", ""),
            vsock_cid=env.get("VSOCK_CID", ""),
        )

    # qemu_setup

    def setup(self) -> None:
        """Normalize the arch and check the profile; says so when there is no KVM."""
        try:
            self.arch = repo.normalize_arch(self.arch or None)
        except repo.UnsupportedArch as e:
            raise Refused(str(e)) from None
        if self.profile not in PROFILES:
            raise Refused(f"QEMU_PROFILE={self.profile} (expected pc, microvm or vz)")
        if self.kvm is None:
            self.kvm = kvm_usable()
        if not self.kvm:
            print("note: /dev/kvm not available, emulating (slow)", flush=True)

    @property
    def qemu(self) -> str:
        return "qemu-system-aarch64" if self.arch == "arm64" else "qemu-system-x86_64"

    @property
    def console(self) -> str:
        if self.profile == "vz":
            return "hvc0"
        return "ttyAMA0" if self.arch == "arm64" else "ttyS0"

    def _accel_machine(self) -> list[str]:
        # arm64 has no default machine type; its console is the virt board's PL011.
        if self.arch == "arm64":
            if self.kvm:
                return ["-machine", "virt,accel=kvm", "-cpu", "host"]
            return ["-machine", "virt", "-cpu", "max"]
        # -cpu max, not qemu64: glibc built for x86-64-v3 refuses a CPU without AVX2.
        args = ["-enable-kvm", "-cpu", "host"] if self.kvm else ["-cpu", "max"]
        # microvm: ACPI off, devices on the command line as Firecracker does; pit=on, or a
        # guest without KVM hangs at "Unable to calibrate against PIT".
        if self.profile == "microvm":
            args += ["-machine", "microvm,acpi=off,pit=on,rtc=on,isa-serial=on"]
        return args

    # qemu_preflight

    def root_cmdline_file(self) -> str:
        return self.root_cmdline or os.path.join(os.path.dirname(self.rootfs), "rootfs.cmdline")

    def preflight(self) -> None:
        hint = f" {self.hint}" if self.hint else ""
        if not shutil.which(self.qemu):
            raise Refused(f"{self.qemu} not found")
        if not os.path.isfile(self.kernel):
            raise Refused(f"missing kernel: {self.kernel}{hint}")
        if not os.path.isfile(self.rootfs):
            raise Refused(f"missing rootfs: {self.rootfs}{hint}")
        cmdline = self.root_cmdline_file()
        if not os.path.isfile(cmdline):
            raise Refused(
                f"missing {cmdline}, the kernel arguments that go with {self.rootfs}\n"
                "       image/build-rootfs.sh writes it next to the image; it carries the "
                "dm-verity root hash"
            )

    # qemu_ssh_setup

    def ssh_setup(
        self, ssh_dir: str = "output/ssh", port: int | None = None, proxy: str = "",
        keep_key: bool = False,
    ) -> int | None:  # fmt: skip
        """Passwordless ssh for this boot: a key pair, a forward and the credential
        (test/qemu-lib.sh says why each). Returns the forwarded port, None with a proxy."""
        if not shutil.which("ssh-keygen"):
            raise Refused("ssh-keygen not found (install an openssh client)")
        d = Path(ssh_dir)
        if not proxy and port is None:
            port = 2222
            while _listening(port):
                port += 1
        key = d / "id_ed25519"
        if keep_key and key.is_file() and (d / "id_ed25519.pub").is_file():
            d.chmod(0o700)
        else:
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, mode=0o700)
            d.chmod(0o700)
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key),
                 "-C", f"kaetzli-ephemeral@{socket.gethostname()}"],
                check=True,
            )  # fmt: skip
        reach = f"    ProxyCommand {proxy}" if proxy else f"    HostName 127.0.0.1\n    Port {port}"
        (d / "config").write_text(
            f"Host kaetzli\n{reach}\n    User root\n"
            f"    IdentityFile {os.path.realpath(key)}\n"
            "    IdentitiesOnly yes\n    BatchMode yes\n    StrictHostKeyChecking no\n"
            "    UserKnownHostsFile /dev/null\n    LogLevel ERROR\n"
        )
        if not proxy:
            self.nic_extra = f",hostfwd=tcp:127.0.0.1:{port}-:22"
        pub = base64.b64encode((d / "id_ed25519.pub").read_bytes()).decode()
        self.append_extra = f" systemd.set_credential_binary=ssh.authorized_keys.root:{pub}"
        self.ssh_dir = ssh_dir
        return None if proxy else port

    # qemu_var_disk

    def _var_disk(self) -> tuple[list[str], str]:
        if not self.var_disk:
            return [], ""
        disk = Path(self.var_disk)
        if not disk.exists():
            disk.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["truncate", "-s", self.var_size, str(disk)], check=True)
        mid = Path(f"{self.var_disk}.machine-id")
        if not mid.is_file() or mid.stat().st_size == 0:
            mid.write_text(os.urandom(16).hex())
        drive = [
            "-drive", f"id=var,file={self.var_disk},format=raw,if=none",
            "-device", "virtio-blk-pci,drive=var",
        ]  # fmt: skip
        append = (
            " systemd.mount-extra=/dev/vdb:/var:ext4:x-systemd.makefs,x-systemd.growfs"
            f" systemd.machine_id={mid.read_text()}"
        )
        return drive, append

    # qemu_sysexts

    def _sysexts(self) -> tuple[list[str], str, str, str]:
        drives: list[str] = []
        append = tables = waitfor = ""
        if not self.sysexts:
            return drives, append, tables, waitfor
        ext_dir = self.extensions or os.path.dirname(self.rootfs)
        nxt = 1 if self.var_disk else 0
        for name in self.sysexts:
            if not os.path.isfile(f"{ext_dir}/extensions/{name}.raw"):
                raise Refused(
                    f"SYSEXTS names {name}, but there is no {ext_dir}/extensions/{name}.raw\n"
                    "       (tools/build-image.sh builds it, tools/fetch-image.sh downloads CI's)"
                )
            for cls, d in (("ext", "extensions"), ("conf", "confexts")):
                file = f"{ext_dir}/{d}/{name}.raw"
                if not os.path.isfile(file):
                    continue
                verity = file.removesuffix(".raw") + ".dm-verity"
                if not os.path.isfile(verity):
                    raise Refused(f"{file} has no {verity} beside it")
                ident = f"{cls}-{name}"
                dev = f"/dev/vd{_LETTERS[nxt]}"
                nxt += 1
                # sed reads lines: a final newline goes, any other stays a newline.
                table = Path(verity).read_text().removesuffix("\n").replace("@DEV@", dev)
                drives += [
                    "-drive", f"file={file},format=raw,if=none,readonly=on,id={ident}",
                    "-device", f"virtio-blk-pci,drive={ident}",
                ]  # fmt: skip
                tables += f";{ident},,,ro,{table}"
                waitfor += f",{dev}"
                append += f" systemd.mount-extra=/dev/mapper/{ident}:/run/{d}/{name}:erofs:ro"
        return drives, append, tables, waitfor

    # qemu_argv

    def argv(self) -> list[str]:
        """The command line after the binary (`self.qemu`). The root is read-only, so one
        file can back any number of guests."""
        # $(cat file) drops every trailing newline.
        root_args = Path(self.root_cmdline_file()).read_text().rstrip("\n")
        var_drive, var_append = self._var_disk()
        ext_drives, ext_append, tables, waitfor = self._sysexts()
        if tables:
            # The extensions' tables join the root's dm-mod.create=, their disks what the
            # kernel waits for (sed's two expressions, line by line).
            lines = []
            for line in root_args.split("\n"):
                line = re.sub(r'"$', lambda _m: tables + '"', line, count=1)
                line = re.sub(
                    r"dm-mod\.waitfor=([^ ]*)",
                    lambda m: f"dm-mod.waitfor={m.group(1)}{waitfor}",
                    line,
                    count=1,
                )
                lines.append(line)
            root_args = "\n".join(lines)
        display = ["-nographic"]
        if self.profile == "pc":
            nic = ["-nic", f"user,model=virtio-net-pci{self.nic_extra}"]
            if self.nic_args:
                nic = list(self.nic_args)
            fwcfg: list[str] = []
            for cred in self.fwcfg_credentials:
                # ${cred%%=*} and ${cred#*=}: without a '=', both are the whole pair.
                name, value = cred.split("=", 1)[0], cred.split("=", 1)[-1]
                fwcfg += ["-fw_cfg", f"name=opt/io.systemd.credentials/{name},string={value}"]
            # The order names the devices: root vda, /var vdb, then extensions.
            devices = [
                "-drive", f"id=root,file={self.rootfs},format=raw,if=none,readonly=on",
                "-device", "virtio-blk-pci,drive=root",
                *var_drive, *ext_drives, *nic, *fwcfg,
            ]  # fmt: skip
            # Empty is unset, as bash's ${QEMU_DISPLAY_ARGS+set} has it for an empty array.
            if self.display_args:
                display = list(self.display_args)
        elif self.profile == "microvm":
            devices = [
                "-drive", f"id=root,file={self.rootfs},format=raw,if=none,readonly=on",
                "-device", "virtio-blk-device,drive=root",
                "-netdev", f"user,id=net0{self.nic_extra}",
                "-device", "virtio-net-device,netdev=net0",
                "-device", "virtio-rng-device",
            ]  # fmt: skip
        else:  # vz
            display = ["-display", "none", "-serial", "none", "-monitor", "none", "-vga", "none"]
            devices = [
                "-drive", f"file={self.rootfs},format=raw,if=virtio,readonly=on",
                "-nic", f"user,model=virtio-net-pci{self.nic_extra}",
                "-chardev", "stdio,id=con,signal=off", "-device", "virtio-serial-pci",
                "-device", "virtconsole,chardev=con",
                "-device", "virtio-rng-pci", "-device", "virtio-balloon-pci",
                "-object", f"memory-backend-memfd,id=mem,size={self.mem}M,share=on",
                "-numa", "node,memdev=mem",
            ]  # fmt: skip
            if self.virtiofs_socket:
                devices += [
                    "-chardev", f"socket,id=vfs,path={self.virtiofs_socket}",
                    "-device", "vhost-user-fs-pci,chardev=vfs,tag=share",
                ]  # fmt: skip
            if self.vsock_cid:
                devices += ["-device", f"vhost-vsock-pci,guest-cid={self.vsock_cid}"]
        append = (
            f"{root_args} console={self.console} init={self.init}"
            f" systemd.set_credential=login.noauth:yes"
            f"{self.append_extra}{var_append}{ext_append}"
        )
        argv = [
            *self._accel_machine(),
            "-m", self.mem, "-smp", self.cpus,
            "-kernel", self.kernel,
            *devices,
            "-append", append,
            *display,
        ]  # fmt: skip
        # A guest's reboot ends qemu, which a test waiting on it wants; reboot=True
        # restarts it in place with the same command line (tools/vm.sh).
        if not self.reboot:
            argv.append("-no-reboot")
        return argv


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def refuse(e: Refused) -> int:
    for i, line in enumerate(str(e).split("\n")):
        print(f"error: {line}" if i == 0 else line, file=sys.stderr)
    return 1
