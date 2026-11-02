"""Hold the amd64 and arm64 kernels to the same features:

    ./test/kernel-parity.py <amd64 config> <arm64 config>

Each is a .config, or a tree with boot/config.

Each kernel is its arch's defconfig plus the same fragments, and the two defconfigs
disagree about a great deal that has nothing to do with the architecture: amd64's had
policy routing, IPsec and SYN cookies and no sock_diag, arm64's had macvlan, tc and five
boards' crypto engines. parity.config in packages/kernel/build.sh decides those once for
both; this fails on any symbol both configs define with different values, so a defconfig
change upstream that brings a difference back is a named failure, not something found on
one arch at runtime (test/listeners.py, on arm64 only, is how the last one was found).

A symbol only one config defines is the architecture's own, or sits under a menu the
other has off — and then the menu is what differs, and is reported. What may differ is
test/kernel-parity-allowed.txt, with the reason; keep it to what is the architecture's
own.

It needs both arches' kernels, so CI runs it in a job of its own after the kernel builds
(`kernel-parity`). Locally, a fetched arm64 kernel config is enough (tools/fetch-image.sh
ARCH=arm64), or the config from CI's package-kernel-arm64 artifact.
"""

from __future__ import annotations

import sys
from pathlib import Path

from kaetzli import repo
from kaetzli.kernel import config

LIST = "test/kernel-parity-allowed.txt"


def allowed(path: Path) -> set[str]:
    return {f"CONFIG_{sym}" for _, sym, _ in config.symbol_list(path)}


def differences(
    amd64: dict[str, str], arm64: dict[str, str], allow: set[str]
) -> list[tuple[str, str, str]]:
    """(symbol, amd64, arm64) for each symbol both define differently, in byte order."""
    return [
        (sym, amd64[sym], arm64[sym])
        for sym in sorted(amd64.keys() & arm64.keys())
        if amd64[sym] != arm64[sym] and sym not in allow
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: test/kernel-parity.py <amd64 config> <arm64 config>", file=sys.stderr)
        return 2
    repo.enter_root()
    paths = [config.path_of(a) for a in argv]
    for p in paths:
        if not p.is_file():
            print(f"error: no kernel config at {p}", file=sys.stderr)
            return 1

    allow = allowed(Path(LIST))
    diffs = differences(config.values(paths[0]), config.values(paths[1]), allow)
    if diffs:
        print("error: the amd64 and arm64 kernels differ (symbol, amd64, arm64):", file=sys.stderr)
        for sym, a, b in diffs:
            print(f"  {sym:<40} {a:<8} {b}", file=sys.stderr)
        print(
            "Decide it for both in parity.config (packages/kernel/build.sh), clear it in"
            " vm.config\n"
            f"if it is hardware, or list it in {LIST} if it is the architecture's own.",
            file=sys.stderr,
        )
        return 1
    print(
        f"kernel parity OK: every symbol both arches define agrees ({len(allow)} allowed to differ)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
