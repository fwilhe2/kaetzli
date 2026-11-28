"""test/kernel-probes.py: boot the image and use each datapath symbol test/kernel-probes.txt
lists, so that a kernel which has the option but not the rule or link a CNI writes fails
here, by name, rather than on a node.

    ./test/kernel-probes.py [rootfs.erofs] [bzImage]

Overrides, from the environment: TIMEOUT, MEM/CPUS, LOG (default
output/kernel-probes-test.log), as in test/container.py.

Each probe runs in a fresh network namespace (`ip netns`), so none touches the guest's own
tables or links and none depends on another's leftovers. Every probe runs, and the test
fails at the end with each failed symbol, kernel-caps.txt's reason for it and what the
command printed. Base image only: iptables, nft, ip and tc are all in it.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from kaetzli import repo
from kaetzli.boot import boot, run
from kaetzli.kernel import config
from kaetzli.qemu import Guest

LIST = "test/kernel-probes.txt"
CAPS = "test/kernel-caps.txt"

DIAGNOSE = "ip netns list; nft list ruleset | head -40; dmesg | tail -20"


def probes(path: Path) -> list[tuple[str, str]]:
    """(symbol, command), in the list's order."""
    return [(sym, cmd) for _, sym, cmd in config.symbol_list(path)]


def reasons(path: Path) -> dict[str, str]:
    return {sym: why for _, sym, why in config.symbol_list(path)}


def probe_line(sym: str, cmd: str) -> str:
    """What is typed for one probe. The verdict is `PROBE <sym> ok` or `failed`, after
    what the command printed; split by quotes so the echo of the typed line cannot
    match."""
    return (
        "ip netns add kp && "
        f"ip netns exec kp bash -c {shlex.quote(cmd)} > /run/kp.out 2>&1; r=$?; "
        "ip netns del kp; "
        f'if [[ $r == 0 ]]; then echo "PROBE {sym} ""ok"; '
        f'else echo "--- {sym}"; cat /run/kp.out; echo "PROBE {sym} ""failed"; fi'
    )


def check(guest: Guest, listed: list[tuple[str, str]], why: dict[str, str]) -> list[str]:
    failed = []
    for sym, cmd in listed:
        guest.mark()
        guest.send(probe_line(sym, cmd))
        if not guest.wait(rf"PROBE {sym} (ok|failed)", 30):
            guest.fail(f"no verdict from the {sym} probe")
        if guest.group() == "ok":
            print(f">> {sym}: ok", flush=True)
            continue
        said = guest.before.rpartition(f"--- {sym}\n")[2].strip()
        print(f">> {sym}: FAILED", flush=True)
        failed.append(f"CONFIG_{sym} — {why.get(sym, '?')}\n      {cmd}\n      {said}")
    return failed


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="output/kernel-probes-test.log")
    listed = probes(repo.ROOT / LIST)
    why = reasons(repo.ROOT / CAPS)

    def test() -> None:
        b.machine.setup()
        b.machine.preflight()
        with b.guest("kernel-probes", diagnose=DIAGNOSE) as guest:
            guest.login()
            failed = check(guest, listed, why)
            if failed:
                print(">> the kernel refused what these need:", file=sys.stderr, flush=True)
                for line in failed:
                    print(f"  {line}", file=sys.stderr, flush=True)
                guest.fail(f"{len(failed)} of {len(listed)} probes failed")
            print(f">> kernel probes OK: all {len(listed)} taken", flush=True)

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
