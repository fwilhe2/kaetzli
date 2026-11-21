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
    """What the config gets wrong: a symbol that is not =y, or a `!SYM` that is."""
    have = set(config.lines(path))
    out = []
    for sym, why in required(entries, config.arch(path)):
        if sym.startswith("!"):
            if f"CONFIG_{sym[1:]}=y" in have:
                out.append(f"CONFIG_{sym[1:]} is set — {why}")
        elif f"CONFIG_{sym}=y" not in have:
            out.append(f"CONFIG_{sym} — {why}")
    return out


def run(argv: list[str], *, script: str, listing: str, problem: str, ok: str) -> int:
    """The check against one list: kernel-caps.py and kernel-hardening.py differ only in
    which list, and in how they say what is wrong."""
    if len(argv) > 1:
        print(f"usage: test/{script} [rootfs]", file=sys.stderr)
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

    entries = config.symbol_list(Path(listing))
    if gone := missing(path, entries):
        print(f"error: {problem}:", file=sys.stderr)
        for line in gone:
            print(f"  {line}", file=sys.stderr)
        return 1

    built_in = sum(line.endswith("=y") for line in config.lines(path))
    n = len(required(entries, config.arch(path)))
    print(ok.format(built_in=built_in, n=n))
    return 0


def main(argv: list[str]) -> int:
    return run(
        argv,
        script="kernel-caps.py",
        listing=LIST,
        problem="the kernel is missing capabilities this image depends on",
        ok="kernel capabilities OK ({built_in} symbols built in, {n} required present)",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
