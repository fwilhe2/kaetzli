"""The boot tests: each module is a test/<name>.py, booting the image through
kaetzli.qemu. What they share is how they start: the image and kernel from the command
line or ROOTFS/KERNEL, relative to the checkout, and INIT, LOG and TIMEOUT from the
environment, as the bash tests read them."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from kaetzli import repo
from kaetzli.qemu import Failed, Guest, Machine, Refused, refuse


@dataclass
class Boot:
    machine: Machine
    log: str
    timeout: str  # as given: it is printed as given

    def guest(self, name: str, **kwargs) -> Guest:
        m = self.machine
        print(
            f">> booting {m.rootfs} with {m.kernel} (init={m.init}), waiting up to {self.timeout}s",
            flush=True,
        )
        return Guest(m.qemu, m.argv(), self.log, float(self.timeout), name=name, **kwargs)


def boot(argv: list[str], *, init: str, log: str) -> Boot:
    """cd to the checkout and make the machine, for the caller to add to and then run
    setup() and preflight() on. Positional: [rootfs.erofs] [bzImage]."""
    repo.enter_root()
    env = os.environ
    rootfs = argv[0] if argv else env.get("ROOTFS") or "output/rootfs.erofs"
    kernel = argv[1] if len(argv) > 1 else env.get("KERNEL") or "rootfs/boot/bzImage"
    machine = Machine.from_env(rootfs=rootfs, kernel=kernel, init=env.get("INIT") or init)
    return Boot(machine, env.get("LOG") or log, env.get("TIMEOUT") or "300")


def run(test: Callable[[], None]) -> int:
    """Run a test body: a refusal (Refused) is exit 1 with its error, a failure (Failed)
    exit 1, its reason printed already."""
    try:
        test()
    except Refused as e:
        return refuse(e)
    except Failed:
        return 1
    return 0
