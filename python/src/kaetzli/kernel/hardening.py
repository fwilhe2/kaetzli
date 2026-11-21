"""Assert the kernel is hardened as test/kernel-hardening.txt says:

    ./test/kernel-hardening.py [rootfs]   # default rootfs/, reads rootfs/boot/config

kernel-caps.py's check against a second list, which also says what must be off. Nothing
in the image fails without these, which is why they need a check of their own: a
defconfig that stops providing one, or a fragment that selects its way around a clear,
would otherwise ship unnoticed.
"""

from __future__ import annotations

import sys

from kaetzli.kernel import caps

LIST = "test/kernel-hardening.txt"


def main(argv: list[str]) -> int:
    return caps.run(
        argv,
        script="kernel-hardening.py",
        listing=LIST,
        problem="the kernel is not hardened as test/kernel-hardening.txt requires",
        ok="kernel hardening OK ({n} requirements met)",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
