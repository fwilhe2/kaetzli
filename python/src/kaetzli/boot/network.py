"""test/network.py: boot with systemd and check the network works from inside the guest,
so a change that leaves the image without an address, DNS or a route fails CI.

    ./test/network.py [rootfs.erofs] [bzImage]

Overrides, from the environment:
  HOST/PORT   what to resolve and connect to  (default example.com / 80)
  TIMEOUT     seconds to wait                 (default 300 — qemu falls back to TCG in
                                               CI, where there is no KVM, and that is slow)
  MEM/CPUS    guest size                      (default 1024 / 2)
  LOG         console transcript              (default output/network-test.log)
  LOGIN_USER  serial console account          (default root; no password, see Guest.login)

Unlike test/boot.py this has to run systemd: addressing is systemd-networkd's job and the
resolver systemd-resolved's, so a raw shell as PID 1 would prove nothing.

The checks come in two rounds, so that a failure says where it broke. The first asks
whether the *system* has a network, with nothing the image had to grow a package for: a
routable link means DHCP answered, `getent hosts` means the resolved stub that
/etc/resolv.conf points at answers, and a TCP connection means routing and the VMM's NAT
work. The second asks whether someone logged in could *see and use* it (issue #77): `ip`
reads the address and route back out of the kernel, `ping` proves an ICMP socket opens,
and `curl` proves TLS — resolution, a chain verified against the shipped CA bundle, and
a request. "The network is broken" and "the tools are broken" stay apart.

Everything past the first check needs the host to have internet access: with qemu's
user-mode networking the guest reaches the outside through it.
"""

from __future__ import annotations

import os
import sys

from kaetzli.boot import boot, run
from kaetzli.qemu import Guest

# How often a round is typed again until it passes.
RETRY = 5

DIAGNOSE = (
    "networkctl --no-pager status; resolvectl --no-pager status; cat /etc/resolv.conf\n"
    "ip -4 address show; ip -4 route show"
)


def check_command(host: str, port: str) -> str:
    """Round one: bash builtins, networkctl and getent only. A console handshake this
    delicate is better off not depending on a package it is not testing."""
    return (
        "[[ $(networkctl --no-pager) == *routable* ]] "
        f"&& getent hosts {host} >/dev/null "
        f"&& (exec 3<>/dev/tcp/{host}/{port}) "
        "&& echo NET-SMOKE'-OK'"
    )


def tools_command(host: str) -> str:
    """Round two: the tools are what it tests, their output still read with [[ ]].

    -4 -oneline, so the link-local IPv6 address every NIC has cannot pass alone. ping goes
    to 127.0.0.1: qemu's user networking forwards ICMP only if the *host* lets an
    unprivileged process open an ICMP socket, which is not the image's property; this asks
    whether the binary runs and gets its socket. curl -f, so an HTTP error fails; -sS, so
    only errors print."""
    return (
        "[[ $(ip -4 -oneline address show scope global) == *inet* ]] "
        "&& [[ $(ip -4 route show default) == default* ]] "
        "&& ping -c1 -W5 127.0.0.1 >/dev/null "
        f"&& curl -fsS -o /dev/null https://{host}/ "
        "&& echo NET-TOOLS'-OK'"
    )


def check(guest: Guest, host: str, port: str) -> None:
    print(">> logged in, checking the network", flush=True)
    # Retried until it passes: DHCP takes a moment.
    guest.retry(
        check_command(host, port),
        "NET-SMOKE-OK",
        RETRY,
        "no address, no DNS or no route out (timed out)",
    )
    print(
        f">> network OK: link routable, {host} resolved, TCP connection to {host}:{port} accepted",
        flush=True,
    )
    print(">> checking the tools a person logged in here would reach for", flush=True)
    # Nothing here waits on a lease, but a few more attempts cost nothing next to failing
    # a CI run on one dropped packet.
    guest.retry(
        tools_command(host),
        "NET-TOOLS-OK",
        RETRY,
        "ip, ping or curl failed (the network itself is up — see the console below)",
    )
    print(
        ">> tools OK: ip shows the lease and a default route, ping runs, "
        f"curl https://{host} verified",
        flush=True,
    )


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="output/network-test.log")
    env = os.environ
    host, port = env.get("HOST") or "example.com", env.get("PORT") or "80"

    def test() -> None:
        b.machine.setup()
        b.machine.preflight()
        with b.guest("network", diagnose=DIAGNOSE) as guest:
            guest.login(env.get("LOGIN_USER") or "root")
            check(guest, host, port)

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
