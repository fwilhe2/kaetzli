"""Assert the kernel can actually run a container — and boot on the VMMs this image
targets, be a Kubernetes node, and be traced by the debug extension:

    ./test/kernel-caps.py [rootfs]      # default rootfs/, reads rootfs/boot/config

What it requires, and why, is test/kernel-caps.txt; that file says how this differs from
the check in packages/kernel/build.sh.

It runs in the `rootfs` CI job, next to check-rootfs-deps.py, because it needs nothing
but the staged tree — no boot, no qemu. test/container.py is the other half: it boots
the thing and starts a real container. Both are worth having, and this one says *which*
capability is missing rather than "crun exited 1".
"""

from __future__ import annotations

import sys
from pathlib import Path

from kaetzli import repo
from kaetzli.kernel import config

LIST = "test/kernel-caps.txt"

Entries = list[tuple[str | None, str, str]]


def required(entries: Entries, arch: str | None) -> list[tuple[str, str]]:
    """(symbol, why) for every architecture, then for this one."""
    return [(sym, why) for section, sym, why in entries if section in (None, arch)]


def missing(path: Path, entries: Entries) -> list[str]:
    have = set(config.lines(path))
    return [
        f"CONFIG_{sym} — {why}"
        for sym, why in required(entries, config.arch(path))
        if f"CONFIG_{sym}=y" not in have
    ]


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print("usage: test/kernel-caps.py [rootfs]", file=sys.stderr)
        return 2
    repo.enter_root()
    path = Path(argv[0] if argv else "rootfs") / "boot" / "config"
    if not path.is_file():
        print(f"error: no kernel config at {path}", file=sys.stderr)
        print(
            "       packages/kernel/build.sh stages it next to the image; build the kernel first",
            file=sys.stderr,
        )
        return 1

    entries = config.symbol_list(Path(LIST))
    if gone := missing(path, entries):
        print("error: the kernel is missing capabilities this image depends on:", file=sys.stderr)
        for line in gone:
            print(f"  {line}", file=sys.stderr)
        return 1

    built_in = sum(line.endswith("=y") for line in config.lines(path))
    n = len(required(entries, config.arch(path)))
    print(f"kernel capabilities OK ({built_in} symbols built in, {n} required present)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
