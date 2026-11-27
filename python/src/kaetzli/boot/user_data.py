"""test/user-data.py: boot as an OpenStack instance would, with a metadata service at
169.254.169.254, and check that kaetzli-user-data.service fetched the user data and ran
it as root, once per instance.

    ./test/user-data.py [rootfs.erofs] [bzImage]

Overrides, from the environment: TIMEOUT, MEM/CPUS, LOG (default
output/user-data-test.log), as in test/podman.py.

The metadata service is this process, re-run by qemu once per guest connection
(`guestfwd=…-cmd:`, the connection on its stdin and stdout), answering from a directory
of files: openstack/latest/meta_data.json and openstack/latest/user_data. guestfwd only
captures addresses inside the user network, so the network is 169.254.169.0/24 for this
boot, which makes the address the guest uses the real one. Each request is appended to a
log beside the files, so the test can also see what the guest asked for.

What is checked in the guest: the unit is active, the user data ran as uid 0, the
instance's UUID is recorded, and a restart does not run it a second time; and on the
host, that the guest asked for both files. A boot without kaetzli.user-data= on the
command line (every other boot test) skips the unit by its condition.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

UUID = "6c1d7f0e-5b55-4b0a-9a0d-6a1f3c2b9e41"
USER_DATA = """#!/bin/bash
echo "$(id -u)" >> /run/user-data-test
"""

CHECK = (
    "systemctl is-system-running --wait >/dev/null; "
    "[[ $(systemctl is-active kaetzli-user-data.service) == active ]] && "
    "[[ $(</run/user-data-test) == 0 ]] && "
    f"[[ $(</var/lib/kaetzli-user-data/done) == {UUID} ]] && "
    "systemctl restart kaetzli-user-data.service && "
    "[[ $(wc -l < /run/user-data-test) == 1 ]] && "
    "echo USER'-DATA-OK'"
)
DIAGNOSE = (
    "systemctl --no-pager status kaetzli-user-data.service; "
    "journalctl --no-pager -b -u kaetzli-user-data -n 40; "
    "cat /run/user-data-test /var/lib/kaetzli-user-data/done; ip -4 addr; ip route"
)


def serve(root: Path, stdin=None, stdout=None) -> None:
    """Answer one HTTP request on stdin/stdout from the files under root, and log its
    path. Enough HTTP for curl: a request line, headers to skip, Connection: close."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    line = stdin.readline().decode("latin-1").split()
    while stdin.readline() not in (b"\r\n", b"\n", b""):
        pass
    path = line[1] if len(line) > 1 else "/"
    with open(root / "requests.log", "a") as log:
        log.write(f"{path}\n")
    target = (root / path.lstrip("/")).resolve()
    if target.is_relative_to(root.resolve()) and target.is_file():
        body, status = target.read_bytes(), "200 OK"
    else:
        body, status = b"not found\n", "404 Not Found"
    stdout.write(
        f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    stdout.flush()


def metadata_dir(root: Path) -> None:
    latest = root / "openstack" / "latest"
    latest.mkdir(parents=True)
    (latest / "meta_data.json").write_text(
        json.dumps({"uuid": UUID, "name": "user-data-test", "hostname": "user-data-test"})
    )
    (latest / "user_data").write_text(USER_DATA)


def nic(root: Path) -> list[str]:
    # The command runs under /bin/sh; option values double their commas, and there are none.
    cmd = shlex.join([sys.executable, "-I", os.path.abspath(__file__), "--serve", str(root)])
    return [
        "-nic",
        f"user,model=virtio-net-pci,net=169.254.169.0/24,guestfwd=tcp:169.254.169.254:80-cmd:{cmd}",
    ]


def main(argv: list[str]) -> int:
    from kaetzli.boot import boot, run
    from kaetzli.qemu import Refused

    b = boot(argv, init="/usr/lib/systemd/systemd", log="output/user-data-test.log")
    root = Path(tempfile.mkdtemp())

    def test() -> None:
        m = b.machine
        m.setup()
        if m.profile != "pc":
            raise Refused(f"test/user-data.py boots the pc profile, not QEMU_PROFILE={m.profile}")
        m.preflight()
        metadata_dir(root)
        m.nic_args = nic(root)
        m.append_extra += " kaetzli.user-data=openstack"
        with b.guest("user-data", diagnose=DIAGNOSE) as guest:
            guest.login()
            guest.send(CHECK)
            if not guest.wait("USER-DATA-OK", 180):
                guest.fail("the user data did not run as root exactly once")
            asked = (root / "requests.log").read_text().split()
            for want in ("/openstack/latest/meta_data.json", "/openstack/latest/user_data"):
                if want not in asked:
                    guest.fail(f"the guest never asked for {want} (asked: {asked})")
            print(
                ">> user data OK: fetched from 169.254.169.254, ran as root, recorded "
                f"for instance {UUID}, not run again on restart",
                flush=True,
            )

    try:
        return run(test)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]))
        sys.exit(0)
    sys.exit(main(sys.argv[1:]))
