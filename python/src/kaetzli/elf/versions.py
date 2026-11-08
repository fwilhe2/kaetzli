"""Report binaries that require a symbol version none of the libraries we ship define.

test/check-rootfs-deps.py answers "is libfoo.so.N in the tree at all?". This answers the
next question: the binary found the library, but does that library actually have the
symbols it wants? Versioned symbols are how glibc keeps old binaries working, so a binary
compiled against a newer libc than the one we ship links fine in the builder and then dies
at exec time with

    ./ls: /lib64/libc.so.6: version `GLIBC_2.44' not found (required by ./ls)

which is exactly the failure mode of compiling against the builder image's glibc instead
of ours (issue #33). Requirements on libraries the tree doesn't ship at all are
test/check-rootfs-deps.py's business and ignored here.

    ./test/check-symbol-versions.py [rootfs-dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

from kaetzli.elf import scan
from kaetzli.report import run


def unsatisfied(elfs: list[scan.Elf]) -> list[tuple[str, str, str]]:
    """(library, version, consumer) for each version a binary needs from a library we
    ship that the library does not define. A library is shipped here when some file
    defines versions under its name (SONAME, or file name without one): one that the
    tree does not have cannot be judged, and check-rootfs-deps says so."""
    defines = {(e.name, v) for e in elfs for v in e.defines}
    shipped = {lib for lib, _ in defines}
    needed = {(lib, v, e.path) for e in elfs for lib, v in e.needs}
    return sorted(n for n in needed if n[0] in shipped and n[:2] not in defines)


def check(root: Path, given: str) -> int:
    missing = unsatisfied(scan.scan(root))
    if not missing:
        print(f"All symbol versions required in {given}/ are defined by the libraries it ships.")
        return 0

    print(f"MISSING symbol versions in {given}/:")
    print()
    for lib, version in sorted({m[:2] for m in missing}):
        print(f"  {lib}: {version}")
        consumers = [p for m_lib, m_version, p in missing if (m_lib, m_version) == (lib, version)]
        for line in scan.head(consumers, 4, "      needed by "):
            print(line)
    print()
    print("These binaries were compiled against a newer library than the image ships and will")
    print("fail at exec time. For glibc that means the build didn't go through our sysroot:")
    print("check that SYSROOT reaches the compiler (builder/build-package.sh) and that the")
    print("package's build system doesn't drop the CFLAGS/LDFLAGS it exports.")
    return 1


def main(argv: list[str]) -> int:
    given, root = scan.tree_or_exit(argv)
    return scan.judged(check, given, root, given)


if __name__ == "__main__":
    sys.exit(run(main, sys.argv[1:]))
