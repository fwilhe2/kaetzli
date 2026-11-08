"""Report shared libraries that binaries in the rootfs need but the rootfs doesn't ship.

Every package is compiled inside the Debian builder image, so a build will happily link
against a library that exists only in that container: the build succeeds, the library is
never staged, and nothing notices until the binary is exec'd in qemu. That is how bash
ended up needing libtinfo.so.6 with no ncurses package. This walks the assembled tree and
reports every unresolved NEEDED entry at once.

    ./test/check-rootfs-deps.py [rootfs-dir] [allowlist]

Libraries listed in test/known-missing-libs.txt are reported but don't fail the run, so
this catches new regressions without being blocked on the existing backlog.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
from pathlib import Path

from kaetzli import repo
from kaetzli.elf import scan
from kaetzli.report import run

ALLOWLIST = repo.ROOT / "test" / "known-missing-libs.txt"
_SKIP = re.compile(r"^\s*(#|$)")


def resolve(root: Path, rel: str, hops: int = 40) -> str | None:
    """rel with every link followed inside root (an absolute target is root's own path,
    not the host's), or None for a loop."""
    parts, done = rel.split("/"), []
    while parts:
        c = parts.pop(0)
        if c in ("", "."):
            continue
        if c == "..":
            if done:
                done.pop()
            continue
        p = root.joinpath(*done, c)
        if p.is_symlink():
            hops -= 1
            if hops < 0:
                return None
            target = os.readlink(p)
            if target.startswith("/"):
                done = []
            parts = target.split("/") + parts
        else:
            done.append(c)
    return "/".join(done)


def provided(root: Path, elfs: list[scan.Elf] | None = None) -> set[str]:
    """What the tree ships: each library's file name, plus its SONAME. A few libraries
    carry no SONAME and the loader resolves those by name via ld.so.conf; a link counts
    under its own name, and its target's SONAME too."""
    sonames = {e.path: e.soname for e in (elfs if elfs is not None else scan.scan(root))}
    names = set()
    candidates = scan.regular_files(root) + scan.symlinks(root)
    for rel in candidates:
        base = rel.rsplit("/", 1)[-1]
        if not fnmatch.fnmatchcase(base, "*.so*"):
            continue
        names.add(base)
        if rel in sonames:
            soname = sonames[rel]
        else:
            target = resolve(root, rel)
            if target is None or not (root / target).is_file():
                continue
            e = scan.read(root, target)
            soname = e.soname if e else None
        if soname:
            names.add(soname)
    return names


def read_allowlist(path: Path) -> set[str]:
    """Every line but comments and blank ones, as it is: an entry is the whole line."""
    if not path.is_file():
        return set()
    return {x for x in path.read_text().splitlines() if not _SKIP.match(x)}


def check(root: Path, given: str, allowlist: Path) -> int:
    elfs = scan.scan(root)
    pairs = [(lib, e.path) for e in elfs for lib in e.needed]
    missing = sorted({lib for lib, _ in pairs} - provided(root, elfs))
    if not missing:
        print(f"All shared library dependencies in {given}/ are satisfied.")
        return 0

    allowed = read_allowlist(allowlist)
    new = [x for x in missing if x not in allowed]
    known = [x for x in missing if x in allowed]

    def report(libs: list[str]) -> None:
        for lib in libs:
            print(f"  {lib}")
            consumers = sorted(path for need, path in pairs if need == lib)
            for line in scan.head(consumers, 4, "      needed by "):
                print(line)

    if known:
        print(f"Known-missing libraries (listed in {allowlist.name}):")
        report(known)
        print()

    if new:
        print(f"NEW unresolved shared library dependencies in {given}/:")
        print()
        report(new)
        print()
        print("Each is linked against a library from the Debian builder image that is never")
        print("installed into the rootfs. Either add a package providing it, or configure the")
        print("package that wants it to build without that optional dependency.")
        return 1

    print("No new unresolved dependencies.")
    return 0


def main(argv: list[str]) -> int:
    given, root = scan.tree_or_exit(argv)
    return scan.judged(check, given, root, given, Path(argv[1]) if len(argv) > 1 else ALLOWLIST)


if __name__ == "__main__":
    sys.exit(run(main, sys.argv[1:]))
