"""test/podman.py: boot the image and use podman the way somebody would: run a container,
reach the network from inside one, and have one started by systemd from a Quadlet file.

    ./test/podman.py [rootfs.erofs] [bzImage]

Overrides, from the environment: TIMEOUT, MEM/CPUS, LOG, LOGIN_USER, as in
test/container.py, and SYSEXTS (default podman).

Four rounds, so a failure says which layer broke:
  1. podman's own report: crun as the runtime, netavark as the network backend, seccomp
     available, overlay storage. Each of those is a build decision somewhere in
     packages/ that can quietly come out differently.
  2. a container runs, in its own pid namespace, with a seccomp filter applied, read from
     /proc/self/status inside it, which is the kernel's answer rather than podman's
     claim. `Seccomp: 2` is filter mode.
  3. the network from inside a container: a name resolves and a TCP connection to it
     opens, which is netavark's bridge, veth and nftables rules, and podman's
     resolv.conf. And a published port: nothing in the test image can listen, so this
     only proves the DNAT and conntrack-mark rules `-p` adds are ones the kernel accepts,
     which is the half that broke, twice, while this was written (NFT_FIB_INET and
     NF_CONNTRACK_MARK in packages/kernel/build.sh).
  4. Quadlet: a .container file becomes a unit at daemon-reload and the container comes
     up under systemd. /run/containers/systemd, because /etc is read-only.

The image is made in the guest, not pulled: a few files out of the guest's own /usr
imported as a one-layer image. Pulling would make the test depend on a registry's
availability and rate limits, which Docker Hub's anonymous ones turn into a coin toss
from a shared CI runner. bash, getent and sleep (the last to keep the Quadlet container
up long enough to be seen running) are all the rounds need, and all three are on the
guest's own disk. Their libraries are named rather than found, because the list is
short.

podman is a system extension (docs/guides/sysext.md), attached as a read-only disk the
way the root is: extensions/podman.raw next to the image.
"""

from __future__ import annotations

import os
import sys

from kaetzli.boot import boot, run
from kaetzli.qemu import Guest

# A marker must not appear verbatim in the command that prints it, because the console
# echoes what is typed until `stty -echo` lands. Inside the single-quoted `bash -c`
# strings the split is "A""B", which the container's shell joins.
INFO_CHECK = (
    "echo PODMAN'-INFO':$(podman info --format "
    "'{{.Host.OCIRuntime.Name}}/{{.Host.NetworkBackend}}/"
    "{{.Host.Security.SECCOMPEnabled}}/{{.Store.GraphDriverName}}')"
)
WANT_INFO = "crun/netavark/true/overlay"

# The image: bash, getent, sleep, their libraries and the loader, plus the merged-/usr
# links. cp -L because the libraries are SONAME symlinks on the guest's disk, and because
# sleep is a link to the coreutils multicall binary, which answers to the name it is
# copied under and needs what every coreutils program needs: libacl and libattr for ls
# and cp, libcap for ls.
IMPORT = (
    "rm -rf /run/img && mkdir -p /run/img/usr/bin /run/img/usr/lib && "
    "cp -L /usr/bin/bash /usr/bin/getent /usr/bin/sleep /run/img/usr/bin/ && "
    "cp -L /usr/lib/libc.so.6 /usr/lib/libtinfo.so.6 /usr/lib/libacl.so.1 "
    "/usr/lib/libattr.so.1 /usr/lib/libcap.so.2 /usr/lib/ld-linux-* /run/img/usr/lib/ && "
    "ln -s usr/bin /run/img/bin && ln -s usr/lib /run/img/lib && "
    "ln -s usr/lib /run/img/lib64 && "
    "tar -C /run/img -c . | podman import - localhost/mini >/dev/null && echo PODMAN'-IMPORT-OK'"
)

RUN = (
    "podman run --rm localhost/mini /usr/bin/bash -c "
    "'while read -r k v; do [[ $k == Seccomp: ]] && s=$v; done < /proc/self/status; "
    'echo pid=$$ seccomp=$s; [[ $$ == 1 && $s == 2 ]] && echo "PODMAN-RUN""-OK"\''
)

NET = (
    "podman run --rm -p 8080:80 localhost/mini /usr/bin/bash -c "
    "'getent hosts example.com && exec 3<>/dev/tcp/example.com/80 "
    '&& echo "PODMAN-NET""-OK"\''
)

# printf rather than a heredoc: this is one line typed at a serial console.
QUADLET = (
    "mkdir -p /run/containers/systemd && "
    "printf '[Container]\\nImage=localhost/mini\\n"
    'Exec=/usr/bin/bash -c "echo hello from quadlet; exec sleep infinity"\\n\' '
    "> /run/containers/systemd/hello.container && systemctl daemon-reload "
    "&& systemctl start hello.service && "
    "sleep 5 && [[ $(systemctl is-active hello.service) == active ]] && echo PODMAN'-QUADLET-OK'"
)

DIAGNOSE = (
    "podman info; podman ps -a; nft list ruleset | head -60\n"
    "systemctl --no-pager status hello.service; journalctl --no-pager -b -n 60 -o short"
)


def step(guest: Guest, command: str, marker: str, failure: str) -> None:
    guest.send(command)
    if not guest.wait(marker, 120):
        guest.fail(failure)


def answer(guest: Guest, command: str, marker: str, failure: str) -> str:
    """Type `command`, which prints `marker` and a word after it, and return the word."""
    guest.mark()
    guest.send(command)
    if not guest.wait(f"{marker}[a-z]", 120):
        guest.fail(failure)
    # The rest of the word, to the end of its line: the match took its first letter.
    first = guest.group(0)[-1]
    if not guest.wait(r"(\S*)\r?\n", 10):
        guest.fail(failure)
    return first + guest.group(1)


def check(guest: Guest) -> None:
    print(">> logged in, asking podman what it is built on", flush=True)
    info = answer(guest, INFO_CHECK, "PODMAN-INFO:", "podman info did not answer")
    if info != WANT_INFO:
        guest.fail(
            f"podman reports runtime/network/seccomp/storage as '{info}', expected {WANT_INFO}"
        )
    print(f">> podman OK: {info}", flush=True)

    print(">> importing a test image from the guest's own files", flush=True)
    step(guest, IMPORT, "PODMAN-IMPORT-OK", "could not build the test image with podman import")

    print(">> running a container", flush=True)
    step(
        guest, RUN, "PODMAN-RUN-OK",
        "the container did not run, or ran without its own pid namespace or a seccomp filter",
    )  # fmt: skip
    print(">> run OK: pid 1 in its own namespace, seccomp in filter mode", flush=True)

    print(">> reaching the network from inside a container", flush=True)
    step(guest, NET, "PODMAN-NET-OK", "no DNS or no outbound TCP from inside a container")
    print(
        ">> network OK: example.com resolved and accepted a connection, with a port published",
        flush=True,
    )

    print(">> starting a container from a Quadlet file", flush=True)
    step(
        guest, QUADLET, "PODMAN-QUADLET-OK",
        "the Quadlet unit was not generated or did not stay up",
    )  # fmt: skip
    print(">> quadlet OK: hello.container became hello.service and is running", flush=True)


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="output/podman-test.log")
    b.machine.sysexts = (os.environ.get("SYSEXTS") or "podman").split()

    def test() -> None:
        b.machine.setup()
        b.machine.preflight()
        with b.guest("podman", diagnose=DIAGNOSE) as guest:
            guest.login(os.environ.get("LOGIN_USER") or "root")
            check(guest)

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
