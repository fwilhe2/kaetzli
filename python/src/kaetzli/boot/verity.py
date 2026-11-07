"""test/verity.py: prove that dm-verity is enforcing, not merely present: tamper with one
byte of a copy of the image and check that the kernel refuses to mount it.

    ./test/verity.py [rootfs.erofs] [bzImage]

Overrides, from the environment: TIMEOUT, MEM/CPUS, LOG, ROOT_CMDLINE (default the
original's rootfs.cmdline).

The byte is the first one in the file, in the 1 KiB EROFS reserves ahead of its
superblock for a boot sector it never has. EROFS does not read it as data, so without
verity the tampered image boots exactly like the original, and that is what makes the
choice sharp rather than lazy: the only thing that can notice is a hash over the whole
4 KiB block it sits in. Changing a byte inside a file would also fail, but would leave
open whether the filesystem or verity caught it.

Two things have to be true: the console carries device-mapper's corruption report, and
the guest never gets as far as a shell. test/boot.py boots the same image untampered with
the same init, which is the control: if that passes and this fails the other way, the
difference is the byte.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from kaetzli.boot import boot, run

# The corruption is the point, so its report is no reason to fail, and neither is the
# stack the kernel prints with its panic over the root it then cannot mount.
EXEMPT = "device-mapper: verity|Call Trace:"

# What the kernel says when a block fails its hash, and what a shell would print.
CORRUPT = "device-mapper: verity.*(corrupted|verification failed)"
PROMPT = "bash-[0-9]"

_REPORT = re.compile(f"{CORRUPT}|Kernel panic|VFS".encode())


def flip(path: Path) -> tuple[int, int]:
    """Byte 0 of `path` XOR 0xff, in place; returns it before and after."""
    with open(path, "r+b") as f:
        old = f.read(1)[0]
        new = old ^ 0xFF
        f.seek(0)
        f.write(bytes([new]))
    return old, new


def main(argv: list[str]) -> int:
    b = boot(argv, init="/bin/bash", log="output/verity-test.log")
    b.machine.init = "/bin/bash"  # fixed: the control is test/boot.py's boot
    m = b.machine
    original = m.rootfs

    def test() -> None:
        # The copy goes next to the console log, and is deleted once the guest has
        # stopped; a failure that exits before then leaves it there to look at. Its kernel
        # arguments are the original's, which is the whole point: a root hash that
        # describes different bytes.
        m.root_cmdline = os.environ.get("ROOT_CMDLINE") or os.path.join(
            os.path.dirname(original), "rootfs.cmdline"
        )
        tampered = Path(os.path.dirname(b.log)) / "verity-tampered.erofs"
        m.setup()
        m.preflight()
        shutil.copyfile(original, tampered)
        old, new = flip(tampered)
        print(f">> flipped byte 0 of a copy of {original} ({old} -> {new})", flush=True)
        m.rootfs = str(tampered)

        with b.guest("verity", exempt=EXEMPT) as guest:
            status = guest.watch(PROMPT, guest.deadline - guest.clock())
            status = {"match": "shell", "exited": "died"}.get(status, status)
            guest.stop()
            tampered.unlink(missing_ok=True)

            if status == "shell":
                guest.fail(
                    "a tampered image booted to a shell — dm-verity is not checking the root"
                )
            log = guest.log.read_bytes()
            if not re.search(CORRUPT.encode(), log):
                guest.fail(
                    f"the tampered image did not boot, but not because verity said so ({status})"
                )

            print(">> verity OK: the tampered image was refused", flush=True)
            report = [line.replace(b"\r", b"") for line in log.split(b"\n") if _REPORT.search(line)]
            for line in report[:4]:
                print(f"   {line.decode(errors='replace')}", flush=True)

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
