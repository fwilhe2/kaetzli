"""test/rootless.py: boot the image, log in as the unprivileged `user`, and run containers
without root.

    ./test/rootless.py [rootfs.erofs] [bzImage]

Overrides, from the environment: TIMEOUT, MEM/CPUS, LOG, SYSEXTS, as in test/podman.py.
The console login is always `user`, the uid-1000 account image/files/etc/passwd ships,
because that is the point.

Rootless needs three things the rootful path does not, and each round checks one:
  1. newuidmap and newgidmap with their file capabilities on the disk image (getcap
     reads them off the mounted root, which proves they survived the tar mkfs.erofs is
     built from, image/build-rootfs.sh), and podman reporting itself rootless.
  2. the ID maps: a container's /proc/self/uid_map has two lines, the user's own uid and
     the 65536 from /etc/subuid. Only newuidmap can write the second, so one line means
     the helper did not run or was not allowed to.
  3. the network, through pasta rather than netavark's bridge: a name resolves and a TCP
     connection opens from inside the container, and a published port's forwarding is
     set up (rootlessport).

The same kind of imported test image as test/podman.py, made by the user into the
user's own storage under ~/.local/share/containers.
"""

from __future__ import annotations

import os
import sys

from kaetzli.boot import boot, run
from kaetzli.boot.podman import answer, step
from kaetzli.qemu import Guest

LOGIN_USER = "user"

# getcap prints `<path> cap_setuid=ep`; matching on the capability rather than the whole
# line keeps this from depending on getcap's spacing.
CAPS = (
    "[[ $(getcap /usr/bin/newuidmap) == *cap_setuid=ep ]] "
    "&& [[ $(getcap /usr/bin/newgidmap) == *cap_setgid=ep ]] && "
    "[[ $(id -u) == 1000 ]] && echo ROOTLESS'-CAPS-OK'"
)

INFO_CHECK = (
    "echo ROOTLESS'-INFO':$(podman info --format "
    "'{{.Host.Security.Rootless}}/{{.Host.OCIRuntime.Name}}/{{.Store.GraphDriverName}}')"
)
WANT_INFO = "true/crun/overlay"

IMPORT = (
    "rm -rf ~/img && mkdir -p ~/img/usr/bin ~/img/usr/lib && "
    "cp -L /usr/bin/bash /usr/bin/getent ~/img/usr/bin/ && "
    "cp -L /usr/lib/libc.so.6 /usr/lib/libtinfo.so.6 /usr/lib/ld-linux-* ~/img/usr/lib/ && "
    "ln -s usr/bin ~/img/bin && ln -s usr/lib ~/img/lib && ln -s usr/lib ~/img/lib64 && "
    "tar -C ~/img -c . | podman import - localhost/mini >/dev/null && echo ROOTLESS'-IMPORT-OK'"
)

# Two lines in uid_map: `0 1000 1` and `1 100000 65536`.
MAP = (
    "podman run --rm localhost/mini /usr/bin/bash -c "
    "'n=0; while read -r a b c; do echo map: $a $b $c; n=$((n+1)); done < /proc/self/uid_map; "
    '[[ $n == 2 ]] && echo "ROOTLESS-MAP""-OK"\''
)

NET = (
    "podman run --rm -p 8080:80 localhost/mini /usr/bin/bash -c "
    "'getent hosts example.com && exec 3<>/dev/tcp/example.com/80 "
    '&& echo "ROOTLESS-NET""-OK"\''
)

DIAGNOSE = (
    "id; getcap /usr/bin/newuidmap /usr/bin/newgidmap; cat /etc/subuid; "
    "podman info 2>&1 | head -60\n"
    "echo $XDG_RUNTIME_DIR; systemctl --user --no-pager status | head -20; "
    "journalctl --no-pager -b -n 40 -o short"
)


def check(guest: Guest) -> None:
    print(f">> logged in as {LOGIN_USER}, checking the ID-map helpers", flush=True)
    guest.send(CAPS)
    if not guest.wait("ROOTLESS-CAPS-OK", 60):
        guest.fail(
            "newuidmap/newgidmap lack their file capabilities on the image, or this is not uid 1000"
        )
    print(">> helpers OK: newuidmap cap_setuid, newgidmap cap_setgid, as uid 1000", flush=True)

    info = answer(
        guest, INFO_CHECK, "ROOTLESS-INFO:", f"podman info did not answer as {LOGIN_USER}"
    )
    if info != WANT_INFO:
        guest.fail(f"podman reports rootless/runtime/storage as '{info}', expected {WANT_INFO}")
    print(f">> podman OK: {info}", flush=True)

    step(
        guest, IMPORT, "ROOTLESS-IMPORT-OK", "could not import a test image into the user's storage"
    )

    print(">> running a rootless container", flush=True)
    step(
        guest, MAP, "ROOTLESS-MAP-OK",
        "the container did not get the subordinate ID range (newuidmap did not map it)",
    )  # fmt: skip
    print(">> ID maps OK: uid 1000 and the 65536 subordinate IDs", flush=True)

    print(">> reaching the network from a rootless container", flush=True)
    step(
        guest, NET, "ROOTLESS-NET-OK",
        "no DNS or TCP from a rootless container, or the published port was refused",
    )  # fmt: skip
    print(">> network OK: through pasta, with a port published", flush=True)


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="output/rootless-test.log")
    b.machine.sysexts = (os.environ.get("SYSEXTS") or "podman").split()

    def test() -> None:
        b.machine.setup()
        b.machine.preflight()
        with b.guest("rootless", diagnose=DIAGNOSE) as guest:
            guest.login(LOGIN_USER)
            check(guest)

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
