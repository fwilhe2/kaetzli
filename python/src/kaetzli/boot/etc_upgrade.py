"""test/etc-upgrade.py: what an update does to a writable /etc kept on a /var disk. Two
boots from one disk, the second with a newer /usr and a newer lower /etc than the first:

    ./test/etc-upgrade.py [rootfs.erofs] [bzImage]

Overrides, from the environment: TIMEOUT (each boot), MEM/CPUS, LOG (default
output/etc-upgrade-test.log, written as -first.log and -second.log).

/etc is an overlay: the image's /etc (and any confext) below, /var/lib/extensions.mutable/etc
above, where writes go (image/files/etc/systemd/confext.conf). An update replaces what is
below and keeps what is above. The update here is a sysext and a confext pair attached as
the VMM attaches them (Machine.sysexts), `kaetzli-upgrade`, at version 1 for the first boot
and 2 for the second: the same layers an image update moves, without building a second
image.

First boot: version 1 is merged, and /etc/kaetzli-upgrade/written is written.

Second boot, at version 2:

  - a file nobody wrote follows the update (followed is 2), and one the update adds is
    there (added)
  - the written file still shadows the update's (written is ours, not 2)
  - the upper layer holds the written file and nothing else: in particular no ld.so.cache
    and no account files, which ldconfig.service and systemd-sysusers.service would write
    now that /usr is newer than /etc/.updated, were they not masked. .updated itself is
    let through, as test/systemd.py lets it through: systemd-update-done writes it after
    every merge (the merged /usr is as new as the merge), and since the image's own mtimes
    are clamped it says nothing about which image /etc was last updated for
  - the boot is `running`

Needs podman (tools/build-extension.sh) and mkfs.ext4, as test/sysext.py does.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from kaetzli.boot import boot, run
from kaetzli.boot.checks import line, verdict
from kaetzli.boot.persistent_var import shutdown
from kaetzli.boot.sysext import build_extension

NAME = "kaetzli-upgrade"
OUT = "output"
UPPER = "/var/lib/extensions.mutable/etc"
OURS = "written on the first boot"


def trees(scratch: Path, version: int) -> tuple[Path, Path]:
    """The sysext and confext trees of one version."""
    usr = scratch / f"sysext-{version}" / "usr/share"
    etc = scratch / f"confext-{version}" / "etc" / NAME
    usr.mkdir(parents=True)
    etc.mkdir(parents=True)
    (usr / NAME).write_text(f"{version}\n")
    (etc / "followed").write_text(f"{version}\n")
    (etc / "written").write_text(f"{version}\n")
    if version > 1:
        (etc / "added").write_text(f"{version}\n")
    return usr.parent.parent, etc.parent.parent


def make(scratch: Path, version: int) -> Path:
    """Build one version with tools/build-extension.sh and move it out of output/ into a
    directory of its own, laid out as Machine.extensions wants. Returns that directory."""
    sysext, confext = trees(scratch, version)
    build_extension("sysext", NAME, sysext)
    build_extension("confext", NAME, confext)
    d = scratch / f"v{version}"
    for kind in ("extensions", "confexts"):
        (d / kind).mkdir(parents=True)
        for suffix in (".raw", ".dm-verity"):
            shutil.move(f"{OUT}/{kind}/{NAME}{suffix}", d / kind / f"{NAME}{suffix}")
    return d


SETTLED = "systemctl is-system-running --wait >/dev/null; "

FIRST_CHECKS = [
    ("version 1 is not merged into /usr", f"[[ $(</usr/share/{NAME}) == 1 ]]"),
    ("version 1 is not merged into /etc", f"[[ $(</etc/{NAME}/followed) == 1 ]]"),
    ("/etc is not writable", f"echo '{OURS}' > /etc/{NAME}/written"),
    ("the write did not land in the upper layer",
     f"[[ $(<{UPPER}/{NAME}/written) == '{OURS}' ]]"),
]  # fmt: skip

SECOND_CHECKS = [
    ("version 2 is not merged into /usr", f"[[ $(</usr/share/{NAME}) == 2 ]]"),
    ("a file nobody wrote did not follow the update", f"[[ $(</etc/{NAME}/followed) == 2 ]]"),
    ("a file the update added is missing", f"[[ $(</etc/{NAME}/added) == 2 ]]"),
    ("the written file lost to the update", f"[[ $(</etc/{NAME}/written) == '{OURS}' ]]"),
    ("ldconfig.service is not masked", "[[ $(systemctl is-enabled ldconfig.service) == masked ]]"),
    ("systemd-sysusers.service is not masked",
     "[[ $(systemctl is-enabled systemd-sysusers.service) == masked ]]"),
    ("the upper layer holds more than the written file and .updated (find below)",
     f"[[ $(cd {UPPER} && find . ! -type d ! -name .updated) == ./{NAME}/written ]]"),
    ("the system is not running", "[[ $(systemctl is-system-running) == running ]]"),
]  # fmt: skip

FIRST_CHECK = line(FIRST_CHECKS, "UPGRADE-FIRST", before=SETTLED)
SECOND_CHECK = line(SECOND_CHECKS, "UPGRADE-SECOND", before=SETTLED)
DIAGNOSE = (
    f"systemd-sysext list; systemd-confext list; (cd {UPPER} && find . | head -40); "
    f"head /etc/{NAME}/*; systemctl --failed --no-pager; "
    "ls -la --time-style=full-iso /etc/.updated /usr"
)


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="")
    log_base = os.environ.get("LOG", "output/etc-upgrade-test.log").removesuffix(".log")
    scratch = Path(OUT) / "etc-upgrade-test"
    disk = scratch / "var.img"

    def start(name: str, extensions: Path):
        m = b.machine
        m.extensions = str(extensions)
        m.setup()
        m.preflight()
        b.log = f"{log_base}-{name}.log"
        return b.guest("etc-upgrade", diagnose=DIAGNOSE)

    def test() -> None:
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True)
        v1, v2 = make(scratch, 1), make(scratch, 2)
        b.machine.sysexts = [NAME]
        b.machine.var_disk = str(disk)

        with start("first", v1) as guest:
            guest.login()
            verdict(guest, FIRST_CHECK, "UPGRADE-FIRST", 180, "no answer from the first boot")
            print(f">> first boot OK: {NAME} 1 merged, /etc written", flush=True)
            shutdown(guest)

        with start("second", v2) as guest:
            guest.login()
            verdict(guest, SECOND_CHECK, "UPGRADE-SECOND", 180, "no answer from the second boot")
            print(
                f">> upgrade OK: {NAME} 2 merged over the same /var; the unwritten file "
                "followed it, the written one kept ours, and the upper layer holds only "
                "that one (no ld.so.cache, no account files)",
                flush=True,
            )

    try:
        return run(test)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
