"""test/sysext.py: boot with system and configuration extensions, from both places a VM
gets them, and check that they are merged, that what they ship runs, and that one built
for another level is refused.

    ./test/sysext.py [rootfs.erofs] [bzImage]

Overrides, from the environment: TIMEOUT, MEM/CPUS, LOG, LOGIN_USER, as in
test/podman.py.

Three toy images, made by tools/build-extension.sh the way any extension is made, and put
on the /var disk before the first boot (mkfs.ext4 -d), the path a real one takes, with
the merge at boot rather than a `systemd-sysext refresh` afterwards:

  kaetzli-hello     sysext   a unit started by Upholds= from a multi-user.target drop-in,
                             which reads a file the confext ships into /run; and a second
                             unit enabled the ordinary way, by a .wants link, which is
                             only reported (docs/guides/sysext.md: a link merged during
                             sysinit should miss the boot transaction)
  kaetzli-hello     confext  /etc/kaetzli-hello.conf
  kaetzli-wrong     sysext   built for SYSEXT_LEVEL=0, and so never merged

and one pair attached the other way, as read-only disks the VMM hands the guest
(Machine.sysexts), mounted at /run/extensions and /run/confexts:

  kaetzli-attached  sysext and confext, a file in /usr and one in /etc

Attached images are verified by dm-verity like the root, from tables on the kernel
command line, so a second, short boot attaches one more:

  kaetzli-tampered  a sysext with one byte changed after it was sealed (byte 0, which
                    EROFS never reads, so only verity can be what notices), which has to
                    produce device-mapper's corruption report instead of being merged

What is checked in the guest: both hellos and both attached are merged and kaetzli-wrong
is not; the Upholds= unit ran and saw the confext's file; /etc/machine-id is still PID
1's bind mount, which the confext overlay is mounted over; and the boot is `running`.

Needs podman (tools/build-extension.sh assembles in the image container, and the /var
disk is made under `podman unshare`) and mkfs.ext4.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from kaetzli.boot import boot, run
from kaetzli.qemu import machine

# Where tools/build-extension.sh writes, and so where the toys are; a test points it
# elsewhere along with build_extension.
OUT = "output"

CHECK = (
    "systemctl is-system-running --wait >/dev/null; "
    "s=$(systemd-sysext list --no-legend); c=$(systemd-confext list --no-legend); "
    "[[ $s == *kaetzli-hello* && $c == *kaetzli-hello* && $s == *kaetzli-attached* "
    "&& $c == *kaetzli-attached* ]] && "
    "[[ $(</usr/share/kaetzli-attached) == attached "
    "&& $(</etc/kaetzli-attached.conf) == attached ]] && "
    "[[ $(findmnt -n -o SOURCE /run/extensions/kaetzli-attached) "
    "== /dev/mapper/ext-kaetzli-attached ]] && "
    "[[ $(systemd-sysext status --no-legend) == *kaetzli-hello* ]] "
    "&& [ ! -e /usr/share/kaetzli-wrong ] && "
    "[[ $(</run/kaetzli-hello) == 'hello from a confext' ]] && "
    "findmnt -rn /etc/machine-id >/dev/null && "
    "[[ $(systemctl is-system-running) == running ]] && "
    'echo "wants-link unit: $([ -e /run/kaetzli-hello-wants ] && echo started '
    '|| echo not started)" && '
    "echo SYSEXT'-OK'"
)
DIAGNOSE = (
    "systemd-sysext status; systemd-confext status; systemd-sysext list; "
    "systemctl --no-pager status kaetzli-hello.service systemd-sysext.service "
    "systemd-confext.service; systemctl --failed --no-pager; "
    "journalctl --no-pager -b -u systemd-sysext -u systemd-confext -n 40; "
    "findmnt /etc /usr /etc/machine-id"
)

# The corruption is the point, and the extension's mount-extra= is a mount the boot
# requires, so the second boot ends in emergency mode.
TAMPERED_EXEMPT = "device-mapper: verity|emergency mode|Emergency Shell"
CORRUPT = "device-mapper: verity.*(corrupted|verification failed)"

_WANTS = re.compile(rb"wants-link unit: [a-z ]*")


def build_extension(kind: str, name: str, tree: Path, level: str = "") -> None:
    env = {**os.environ, "LEVEL": level} if level else None
    subprocess.run(["./tools/build-extension.sh", kind, name, str(tree)], env=env, check=True)


def _unit(path: Path, description: str, command: str) -> None:
    path.write_text(
        f"[Unit]\nDescription={description}\n\n[Service]\nType=oneshot\n"
        f"RemainAfterExit=yes\nExecStart=/usr/bin/bash -c '{command}'\n"
    )


def toys() -> list[Path]:
    out = Path(OUT)
    return [
        *out.glob("extensions/kaetzli-hello.*"),
        *out.glob("extensions/kaetzli-wrong.*"),
        *out.glob("confexts/kaetzli-hello.*"),
        *out.glob("extensions/kaetzli-attached.*"),
        *out.glob("confexts/kaetzli-attached.*"),
        *out.glob("extensions/kaetzli-tampered.*"),
    ]


def remove(scratch: Path) -> None:
    shutil.rmtree(scratch, ignore_errors=True)
    for toy in toys():
        toy.unlink(missing_ok=True)


def make(scratch: Path) -> Path:
    """The five toy images, the tampered one tampered with, and the /var disk holding the
    three systemd finds there. Returns the disk."""
    out = Path(OUT)
    # The sysext.
    sys_dir = scratch / "hello-sysext/usr/lib/systemd/system"
    (sys_dir / "multi-user.target.d").mkdir(parents=True)
    (sys_dir / "multi-user.target.wants").mkdir()
    _unit(
        sys_dir / "kaetzli-hello.service",
        "Hello from a system extension",
        "cat /etc/kaetzli-hello.conf > /run/kaetzli-hello",
    )
    (sys_dir / "multi-user.target.d/10-kaetzli-hello.conf").write_text(
        "[Unit]\nUpholds=kaetzli-hello.service\n"
    )
    _unit(
        sys_dir / "kaetzli-hello-wants.service",
        "Hello from a system extension, by .wants",
        "touch /run/kaetzli-hello-wants",
    )
    (sys_dir / "multi-user.target.wants/kaetzli-hello-wants.service").symlink_to(
        "../kaetzli-hello-wants.service"
    )
    build_extension("sysext", "kaetzli-hello", scratch / "hello-sysext")

    # The confext.
    (scratch / "hello-confext/etc").mkdir(parents=True)
    (scratch / "hello-confext/etc/kaetzli-hello.conf").write_text("hello from a confext\n")
    build_extension("confext", "kaetzli-hello", scratch / "hello-confext")

    # The one with the wrong level.
    (scratch / "wrong/usr/share").mkdir(parents=True)
    (scratch / "wrong/usr/share/kaetzli-wrong").write_text("wrong\n")
    build_extension("sysext", "kaetzli-wrong", scratch / "wrong", level="0")

    # The pair the VMM attaches.
    (scratch / "attached-sysext/usr/share").mkdir(parents=True)
    (scratch / "attached-confext/etc").mkdir(parents=True)
    (scratch / "attached-sysext/usr/share/kaetzli-attached").write_text("attached\n")
    (scratch / "attached-confext/etc/kaetzli-attached.conf").write_text("attached\n")
    build_extension("sysext", "kaetzli-attached", scratch / "attached-sysext")
    build_extension("confext", "kaetzli-attached", scratch / "attached-confext")

    # The one that has been tampered with, for the second boot.
    (scratch / "tampered/usr/share").mkdir(parents=True)
    (scratch / "tampered/usr/share/kaetzli-tampered").write_text("tampered\n")
    build_extension("sysext", "kaetzli-tampered", scratch / "tampered")
    with open(out / "extensions/kaetzli-tampered.raw", "r+b") as f:
        byte = f.read(1)[0]
        f.seek(0)
        f.write(bytes([(byte + 1) % 256]))

    # The /var disk, with the three images where systemd looks for them.
    (scratch / "var/lib/extensions").mkdir(parents=True)
    (scratch / "var/lib/confexts").mkdir()
    for name in ("kaetzli-hello.raw", "kaetzli-wrong.raw"):
        shutil.copy(out / "extensions" / name, scratch / "var/lib/extensions/")
    shutil.copy(out / "confexts/kaetzli-hello.raw", scratch / "var/lib/confexts/")
    disk = scratch / "var.img"
    machine.seed_var_disk(disk, "1G", scratch / "var")
    return disk


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="output/sysext-test.log")
    log = b.log

    def test() -> None:
        scratch = Path(OUT) / "sysext-test"
        # tools/build-extension.sh writes where real extensions go, so the toys are gone
        # again when this ends, whichever way.
        remove(scratch)
        scratch.mkdir(parents=True)
        try:
            boots(make(scratch))
        finally:
            remove(scratch)

    def boots(disk: Path) -> None:
        m = b.machine
        m.var_disk = str(disk)
        m.sysexts = ["kaetzli-attached"]
        m.extensions = OUT
        m.setup()
        m.preflight()
        with b.guest("sysext", diagnose=DIAGNOSE) as guest:
            guest.login(os.environ.get("LOGIN_USER") or "root")
            guest.mark()
            guest.send(CHECK)
            if not guest.wait("SYSEXT-OK", 120):
                guest.fail("the extensions were not merged, or what they ship did not run")
            wants = _WANTS.findall(Path(log).read_bytes())
            if wants:
                print(f">> {wants[-1].decode()}", flush=True)
            print(
                ">> sysext OK: kaetzli-hello merged from the /var disk and kaetzli-attached "
                "from read-only disks, the Upholds= unit ran, kaetzli-wrong (SYSEXT_LEVEL=0) "
                "refused",
                flush=True,
            )

        # The second boot: the tampered image attached and nothing else. The kernel
        # builds its verity device from the table before userspace starts, and reading
        # the image through it has to fail with device-mapper's report, on the console,
        # where the kernel logs it.
        b.log = f"{log.removesuffix('.log')}-tampered.log"
        m.var_disk = ""
        m.sysexts = ["kaetzli-tampered"]
        # DIAGNOSE is still the first boot's, as it was in bash.
        with b.guest("sysext", diagnose=DIAGNOSE, exempt=TAMPERED_EXEMPT) as guest:
            if not guest.wait(CORRUPT, 180):
                guest.fail("a tampered extension was read without dm-verity objecting")
            print(">> sysext OK: a tampered extension is refused by dm-verity, as the root is")

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
