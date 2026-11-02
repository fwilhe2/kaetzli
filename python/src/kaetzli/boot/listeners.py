"""test/listeners.py: boot the image and require that what listens on the network is
exactly what we expect, so a version bump that opens a new port (an LLMNR or mDNS
responder in resolved, a debug endpoint in a daemon) fails CI rather than shipping.
test/image-files/ holds the files to a list; this holds the sockets to one.

    ./test/listeners.py [rootfs.erofs] [bzImage]

One boot per round: the base image, then with each extension that starts a daemon at
boot attached (SYSEXTS): kubernetes, for CRI-O. podman's starts nothing at boot, so a
round of its own would boot the base again. kubelet runs only once kubeadm has
configured it, so its :10250 is test/k8s.sh's.

Overrides, from the environment:
  ROUNDS     which rounds          (default: base kubernetes)
  TIMEOUT    seconds to wait       (default 300)
  MEM/CPUS   guest size            (default 1024 / 2)
  LOG        console transcript    (default output/listeners-test.log, -<round> after it)

Only sockets another machine could reach count: loopback addresses are dropped (that is
resolved's stub on 127.0.0.53/54, among others), and so are unix and vsock sockets, which
`ss -ntu` does not list. From Flatcar's cl.network.listeners and LinuxKit's test that a
default image opens no ports.
"""

from __future__ import annotations

import os
import re
import sys

from kaetzli.boot import boot, run

# What each round must listen on, as "proto address:port process". A round lists only
# what it adds to the base. sshd, on both families, is all the base has; CRI-O's
# streaming server is on loopback.
EXPECTED: dict[str, list[str]] = {
    "base": ["tcp 0.0.0.0:22 sshd", "tcp [::]:22 sshd"],
    "kubernetes": [],
}
WHERE = "python/src/kaetzli/boot/listeners.py"

# -H no header, -l listening (for udp: bound and unconnected), -n numeric, -p the process.
LIST = (
    "systemctl is-system-running --wait >/dev/null; "
    "while IFS= read -r l; do echo \"LSN> $l\"; done < <(ss -Hlntup); echo LISTENERS'-DONE'"
)

_LOOPBACK = ("127.", "[::1]", "[::ffff:127.")
_USERS = re.compile(r'^users:\(\("([^"]*)"')


def reduce(console: str) -> list[str]:
    """The reachable listeners in ss's output, as "proto address:port process", sorted.

    ss's columns are netid, state, recv-q, send-q, local, peer, process; the process is
    users:(("name",pid=…,fd=…),…), of which the first name is enough."""
    got = set()
    for line in console.split("\n"):
        if not line.startswith("LSN> "):
            continue
        # bash's `read -r proto _ _ _ local _ users`: the last takes the rest.
        fields = line[len("LSN> ") :].split(None, 6)
        fields += [""] * (7 - len(fields))
        proto, local, users = fields[0], fields[4], fields[6].strip()
        if local.startswith(_LOOPBACK):
            continue
        # IPv6's any address is [::] through sock_diag and * when ss falls back to
        # /proc/net (amd64's kernel has no INET_DIAG, arm64's defconfig turns it on).
        if local.startswith("*:"):
            local = "[::]:" + local[2:]
        m = _USERS.match(users)
        got.add(f"{proto} {local} {m.group(1) if m and m.group(1) else '?'}")
    return sorted(got)


def _words(lines: list[str]) -> str:
    return " ".join(word for line in lines for word in line.split())


def round_(argv: list[str], name: str, base_log: str) -> None:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="")
    b.log = f"{base_log.removesuffix('.log')}-{name}.log"  # ${BASE_LOG%.log}-$name.log
    b.machine.sysexts = [] if name == "base" else [name]
    want = sorted(set(EXPECTED["base"] + EXPECTED.get(name, [])))
    b.machine.setup()
    b.machine.preflight()
    with b.guest(f"listeners-{name}") as guest:
        guest.login(os.environ.get("LOGIN_USER") or "root")
        guest.mark()
        guest.send(LIST)
        if not guest.wait("LISTENERS-DONE", guest.deadline - guest.clock()):
            guest.fail("ss never finished")
        got = reduce(guest.before)
        print(f">> {name} listens on:", flush=True)
        for line in got or ["<nothing>"]:
            print(f"   {line}", flush=True)
        extra = [g for g in got if g not in want]
        missing = [w for w in want if w not in got]
        if extra:
            guest.fail(
                f"{name} listens on what nobody expected (extra: {_words(extra)}) — close "
                f'it, or add it to EXPECTED["{name}"] in {WHERE}'
            )
        if missing:
            guest.fail(f"{name} does not listen on {_words(missing)}")
    print(f">> listeners OK: {name}", flush=True)


def main(argv: list[str]) -> int:
    env = os.environ
    rounds = (env.get("ROUNDS") or "base kubernetes").split()
    base_log = env.get("LOG") or "output/listeners-test.log"

    def test() -> None:
        for name in rounds:
            round_(argv, name, base_log)

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
