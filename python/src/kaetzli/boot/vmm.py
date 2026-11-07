"""test/vmm.py: boot the image on the shapes of machine the kernel is built for besides
qemu's own.

    ./test/vmm.py [rootfs.erofs] [kernel] [microvm|vz ...]   # default: both

Overrides, from the environment: TIMEOUT, MEM/CPUS, INIT, ARCH, LOGIN_USER, SSH_DIR. The
transcripts are output/vmm-<profile>-test.log.

packages/kernel/build.sh (guest.config) builds in what Firecracker and Apple's
Virtualization.framework give a guest, and nothing else here would notice if it went:
every other test boots qemu's PCI machine, where virtio-pci and a UART are enough. qemu
cannot be either VMM, but it can give a guest the same devices (Machine.profile), and the
devices are what the kernel sees.

  microvm  Firecracker. No PCI device at all: disk, network and entropy are virtio over
           MMIO, and on amd64 they are announced the way Firecracker announces them, as
           virtio_mmio.device= arguments on the kernel command line. The kernel is an
           uncompressed vmlinux booted through its PVH entry point, which is what
           Firecracker loads on x86, extracted from the bzImage this repository ships, so
           the check is on the artifact rather than on a build tree. arm64's Image is
           already uncompressed, and is what Firecracker loads there.
  vz       Virtualization.framework. The console is a virtio console (hvc0) with no UART
           behind it, a host directory is a virtiofs mount, and the host reaches the guest
           over vsock: here by logging in with ssh through it, as vfkit-style tools do, to
           sshd's vsock listener that systemd-ssh-generator sets up on its own when a guest
           has a vsock device. virtiofs needs virtiofsd on the host and vsock needs
           /dev/vhost-vsock; each part is skipped, with a note, where they are missing.

Both rounds boot systemd for real and log in, and both have to reach `running`: a kernel
that boots but whose console, entropy or disk only half works shows up as a failed unit
before it shows up anywhere else.
"""

from __future__ import annotations

import contextlib
import gzip
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

from kaetzli import repo
from kaetzli.boot import boot, run
from kaetzli.qemu import Guest, Refused

DIAGNOSE = "systemctl --failed --no-pager --plain; journalctl -p err -b --no-pager -o short"

CHECK = "echo VMM'-STATE':$(systemctl is-system-running --wait)"

# Which virtio drivers bound to a device: the proof a device was found, not just offered.
# The trailing echo ends the line, so the names are not on the line with ask's end marker.
BOUND = (
    'for d in /sys/bus/virtio/devices/*/driver; do basename "$(readlink "$d")"; done '
    '| sort -u | tr "\\n" " "; echo'
)

VIRTIOFSD = ("/usr/libexec/virtiofsd", "/usr/lib/qemu/virtiofsd")


def find_virtiofsd() -> str:
    return shutil.which("virtiofsd") or next((p for p in VIRTIOFSD if os.path.exists(p)), "")


def vsock_usable() -> bool:
    return os.access("/dev/vhost-vsock", os.W_OK) and shutil.which("socat") is not None


def extract_vmlinux(bzimage: str, out: str) -> None:
    """tools/lib.sh's extract_vmlinux: the ELF in the bzImage's gzip payload."""
    data = Path(bzimage).read_bytes()
    off = data.find(b"\x1f\x8b\x08")
    if off < 0:
        raise Refused(f"no gzip payload in {bzimage}")
    # gzip -dc on what follows: the payload, and whatever trails it, ignored.
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        elf = d.decompress(data[off:])
    except (zlib.error, gzip.BadGzipFile):
        elf = b""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(elf)
    if elf[:4] != b"\x7fELF":
        raise Refused(f"{bzimage} did not unpack to an ELF")


def has_pvh_note(path: str) -> bool:
    p = subprocess.run(["readelf", "-n", path], capture_output=True, text=True, check=False)
    return "Xen" in p.stdout


def ask(guest: Guest, name: str, command: str) -> str:
    """One shell command, one marked answer: the lines between its markers. The markers
    are assembled by the guest's shell, so the echo of what was typed cannot satisfy the
    wait."""
    guest.mark()
    guest.send(f"echo VMM-{name}'-BEGIN'; {command}; echo VMM-{name}'-END'")
    if not guest.wait(f"VMM-{name}-END", 60):
        guest.fail(f"no answer to: {command}")
    lines = (guest.before + guest.group(0)).split("\n")
    begin = next((i for i, line in enumerate(lines) if f"VMM-{name}-BEGIN" in line), None)
    if begin is None:
        return ""
    end = next(
        (i for i in range(begin + 1, len(lines)) if f"VMM-{name}-END" in lines[i]), len(lines)
    )
    return "\n".join(lines[begin + 1 : end]).rstrip("\n")


def _int(text: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        return -1


def check_running(guest: Guest, profile: str) -> None:
    guest.login(os.environ.get("LOGIN_USER") or "root")
    # Kernel messages go to the same console the answers are read from; past this point
    # only a panic (KERN_EMERG) should get through.
    guest.send("dmesg -n 1")
    guest.send(CHECK)
    if not guest.wait("VMM-STATE:", guest.deadline - guest.clock()):
        guest.fail("systemd never reported a final state")
    result = ""
    if guest.wait(r"([^\n]*)\n", 10):
        m = re.match(r"[a-z][a-z-]*", guest.group(1))
        result = m.group(0) if m else ""
    if result != "running":
        guest.fail(f"system is {result or 'in no state at all'}, not running")
    print(f">> {profile}: system is running", flush=True)


def expect_driver(guest: Guest, drivers: str, name: str) -> None:
    if name not in drivers.split():
        guest.fail(f"no virtio device bound to {name} (bound: {drivers})")


class Rounds:
    def __init__(self, rootfs: str, bzimage: str):
        self.rootfs = rootfs
        self.bzimage = bzimage
        self.arch = repo.normalize_arch(os.environ.get("ARCH") or None)

    def boot(self, profile: str, kernel: str):
        b = boot([self.rootfs, kernel], init="/usr/lib/systemd/systemd", log="")
        b.log = f"output/vmm-{profile}-test.log"
        b.machine.profile = profile
        return b

    def microvm(self) -> None:
        kernel = self.bzimage
        if self.arch == "amd64":
            kernel = "output/vmlinux"
            extract_vmlinux(self.bzimage, kernel)
            if not has_pvh_note(kernel):
                raise Refused(f"{kernel} has no PVH entry note; Firecracker cannot boot it")
        b = self.boot("microvm", kernel)
        b.machine.setup()
        b.machine.preflight()
        with b.guest("vmm-microvm", diagnose=DIAGNOSE) as guest:
            check_running(guest, "microvm")
            pci = ask(guest, "PCI", "ls /sys/bus/pci/devices 2>/dev/null | wc -l")
            drivers = ask(guest, "DRIVERS", BOUND)
            print(f">> microvm: virtio drivers bound: {drivers}", flush=True)
            for name in ("virtio_blk", "virtio_net", "virtio_rng"):
                expect_driver(guest, drivers, name)
            if self.arch == "amd64":
                if _int(pci) != 0:
                    guest.fail(f"the microvm guest has {pci} PCI devices, expected none")
                cmdline = ask(
                    guest, "CMDLINE", 'grep -o "virtio_mmio.device=[^ ]*" /proc/cmdline | wc -l'
                )
                if _int(cmdline) < 2:
                    guest.fail(f"devices were not announced on the command line ({cmdline})")
                print(
                    f">> microvm: no PCI, {cmdline} virtio-mmio devices from the command line, "
                    "PVH boot",
                    flush=True,
                )

    def vz(self) -> None:
        share = Path(tempfile.mkdtemp())
        token = os.urandom(8).hex()
        (share / "token").write_text(token + "\n")
        socket_path = ""
        virtiofsd_proc = None
        b = self.boot("vz", self.bzimage)
        m = b.machine
        cleanup: list[str | Path] = []
        try:
            virtiofsd = find_virtiofsd()
            if virtiofsd:
                socket_path = f"{share}.sock"
                virtiofsd_proc = subprocess.Popen(
                    [virtiofsd, f"--socket-path={socket_path}", f"--shared-dir={share}",
                     "--sandbox=none", "--cache=never"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )  # fmt: skip
                for _ in range(50):
                    if os.path.exists(socket_path):
                        break
                    time.sleep(0.1)
                if not os.path.exists(socket_path):
                    raise Refused("virtiofsd did not start")
                m.virtiofs_socket = socket_path
            else:
                print("note: no virtiofsd on this host, skipping the virtiofs check", flush=True)
            if vsock_usable():
                m.vsock_cid = str(1000 + random.randrange(30000))
                ssh_dir = os.environ.get("SSH_DIR") or "output/ssh"
                port = int(os.environ["SSH_PORT"]) if os.environ.get("SSH_PORT") else None
                m.ssh_setup(ssh_dir, port=port, proxy=os.environ.get("SSH_PROXY", ""))
                cleanup.append(ssh_dir)
            else:
                print(
                    "note: no /dev/vhost-vsock or socat on this host, skipping the vsock check",
                    flush=True,
                )

            m.setup()
            m.preflight()
            with b.guest("vmm-vz", diagnose=DIAGNOSE, cleanup=cleanup) as guest:
                check_running(guest, "vz")
                drivers = ask(guest, "DRIVERS", BOUND)
                print(f">> vz: virtio drivers bound: {drivers}", flush=True)
                for name in ("virtio_console", "virtio_rng", "virtio_balloon"):
                    expect_driver(guest, drivers, name)
                if ask(guest, "TTY", "tty") != "/dev/hvc0":
                    guest.fail("the login is not on hvc0")
                if socket_path:
                    self.virtiofs(guest, drivers, token)
                if m.vsock_cid:
                    self.vsock(guest, drivers, m.ssh_dir, m.vsock_cid)
        finally:
            if virtiofsd_proc is not None:
                with contextlib.suppress(OSError):
                    virtiofsd_proc.kill()
                virtiofsd_proc.wait()
            shutil.rmtree(share, ignore_errors=True)
            if socket_path:
                for p in (socket_path, f"{socket_path}.pid"):
                    with contextlib.suppress(OSError):
                        os.unlink(p)

    def virtiofs(self, guest: Guest, drivers: str, token: str) -> None:
        expect_driver(guest, drivers, "virtiofs")
        # A line of the answer, not all of it: the kernel logs to the same console, and
        # virtiofsd's reply to the unmount's FUSE_DESTROY draws a harmless "response too
        # short" from it that can land in the middle.
        got = ask(guest, "VIRTIOFS", "mount -t virtiofs share /mnt && cat /mnt/token; umount /mnt")
        if token not in got.split("\n"):
            guest.fail(f"read '{got}' through virtiofs, the host wrote '{token}'")
        print(">> vz: virtiofs mount reads what the host wrote", flush=True)

    def vsock(self, guest: Guest, drivers: str, ssh_dir: str, cid: str) -> None:
        expect_driver(guest, drivers, "vmw_vsock_virtio_transport")
        try:
            p = subprocess.run(
                ["ssh", "-F", f"{ssh_dir}/config",
                 "-o", f"ProxyCommand=socat - VSOCK-CONNECT:{cid}:22", "kaetzli",
                 "cat /proc/sys/kernel/random/boot_id"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, timeout=60, check=False,
            )  # fmt: skip
            ok = p.returncode == 0
            got = p.stdout.decode(errors="replace").rstrip("\n")
        except subprocess.TimeoutExpired:
            ok, got = False, ""
        if not ok:
            guest.fail(f"ssh over vsock (cid {cid}) failed")
        if got != ask(guest, "BOOTID", "cat /proc/sys/kernel/random/boot_id"):
            guest.fail(f"ssh over vsock reached a different machine ({got})")
        print(">> vz: ssh over vsock reaches the guest", flush=True)


def main(argv: list[str]) -> int:
    repo.enter_root()
    env = os.environ
    rootfs = argv[0] if argv else env.get("ROOTFS") or "output/rootfs.erofs"
    bzimage = argv[1] if len(argv) > 1 else env.get("KERNEL") or "rootfs/boot/bzImage"
    profiles = argv[2:] or ["microvm", "vz"]
    rounds = Rounds(rootfs, bzimage)
    for p in profiles:
        if p not in ("microvm", "vz"):
            print(f"error: unknown profile {p} (microvm, vz)", file=sys.stderr)
            return 2
        rc = run(getattr(rounds, p))
        if rc:
            return rc
    print(f">> vmm OK: {' '.join(profiles)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
