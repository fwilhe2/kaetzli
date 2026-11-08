"""Report binaries that call a function none of the libraries they load defines.

The third of the library checks, and the one that holds the builder coupling to account
(docs/guides/linking.md). test/check-rootfs-deps.py asks "is libfoo.so.N in the tree?",
test/check-symbol-versions.py asks "does it define the symbol *versions* the binary
wants?", and that second question only exists for libraries that version their symbols.
glib, json-c, pcre2, jansson and libnftnl do not: a binary compiled against sid's newer
header for one of them names a plain `g_foo`, which nothing can hold against a version,
and the first sign is

    conmon: symbol lookup error: conmon: undefined symbol: g_foo

at exec time, or later still: symbols are bound lazily, so only when that code path first
runs. This asks the question directly: for every undefined symbol in every ELF object, is
it defined somewhere in that object's NEEDED closure (itself, what it needs, what those
need, ...)?

Versions are ignored here, that being the other check's job, and so are *weak* undefined
symbols, which are allowed to stay unresolved: that is how a library asks "is this
optional function present?" and gets NULL when it is not. Only the dynamic symbol table
is read: it is the only one the loader reads and the only one a stripped binary keeps.

Some objects resolve symbols from outside their own NEEDED closure, legitimately: plugins
expect the program that dlopens them to provide symbols (bash's loadable builtins call
bash's own functions). Those are not missing libraries and not skew, so
test/known-unresolved-symbols.txt lists them, by SONAME, the way
test/known-missing-libs.txt lists the library backlog. An object whose NEEDED names a
library the tree does not ship is test/check-rootfs-deps.py's business and skipped.

    ./test/check-symbol-resolution.py [rootfs-dir]
"""

from __future__ import annotations

import os
import sys
from collections import deque
from pathlib import Path

from kaetzli import repo
from kaetzli.elf import scan
from kaetzli.report import run

ALLOWLIST = repo.ROOT / "test" / "known-unresolved-symbols.txt"


def read_allowlist(path: Path) -> set[tuple[str, str]]:
    """Entries are "<soname> <symbol>", # comments: the SONAME rather than a path, so an
    entry holds wherever the library is found. An object with no SONAME, a plugin or a
    program, is named by its file name."""
    if not path.is_file():
        return set()
    out = set()
    for line in path.read_text().splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) == 2:
            out.add((fields[0], fields[1]))
    return out


def libraries(root: Path, elfs: list[scan.Elf]) -> dict[str, scan.Elf]:
    """What a NEEDED name loads. The loader resolves one by *file name*, not by SONAME,
    so a symlink is as good a provider as the library's own name: bash asks for
    libtinfo.so.6, which is a link to ncurses' libtinfow.so.6, whose SONAME says
    libtinfow. Several files can have one name (the same library under two names is a
    link, and only one is a regular file); any of them defines the same symbols."""
    lib_of: dict[str, scan.Elf] = {}
    for e in elfs:
        if e.has_dynsym:
            lib_of.setdefault(e.name, e)
            lib_of.setdefault(e.path.rsplit("/", 1)[-1], e)
    links = [
        (rel.rsplit("/", 1)[-1], os.readlink(root / rel).rsplit("/", 1)[-1])
        for rel in scan.symlinks(root)
    ]
    # Followed name by name until nothing changes: libtinfo.so.6 reaches a file only
    # through libtinfow.so.6.
    changed = True
    while changed:
        changed = False
        for name, target in links:
            if name not in lib_of and target in lib_of:
                lib_of[name] = lib_of[target]
                changed = True
    return lib_of


def closure(e: scan.Elf, lib_of: dict[str, scan.Elf]) -> list[scan.Elf] | None:
    """e and everything its NEEDED loads, breadth first; None when a NEEDED is not in
    the tree, which leaves the object unjudged."""
    seen, order, queue, complete = {e.path}, [e], deque([e]), True
    while queue:
        for name in queue.popleft().needed:
            lib = lib_of.get(name)
            if lib is None:
                complete = False
                continue
            if lib.path not in seen:
                seen.add(lib.path)
                order.append(lib)
                queue.append(lib)
    return order if complete else None


def findings(
    root: Path, elfs: list[scan.Elf], allowed: set[tuple[str, str]]
) -> tuple[list[tuple[str, str]], list[str]]:
    """(object, symbol) for every symbol nothing in the object's closure defines, and the
    objects that could not be judged."""
    lib_of = libraries(root, elfs)
    unresolved, skipped = set(), set()
    for e in elfs:
        if not e.undefined:
            continue
        loaded = closure(e, lib_of)
        if loaded is None:
            skipped.add(e.path)
            continue
        for sym in e.undefined:
            if (e.name, sym) in allowed:
                continue
            if not any(sym in lib.defined for lib in loaded):
                unresolved.add((e.path, sym))
    return sorted(unresolved), sorted(skipped)


def check(root: Path, given: str, allowlist: Path) -> int:
    unresolved, skipped = findings(root, scan.scan(root, symbols=True), read_allowlist(allowlist))
    # Said every time, pass or fail, so that a backlog in known-missing-libs.txt is never
    # mistaken for coverage: those objects were not checked at all.
    if skipped:
        print(f"note: {len(skipped)} object(s) need a library {given}/ does not ship and were not checked;")  # fmt: skip # noqa: E501
        print("      test/check-rootfs-deps.py reports which.")

    if not unresolved:
        print(f"Every symbol an object in {given}/ uses is defined by the libraries it loads.")
        return 0

    print(f"UNRESOLVED symbols in {given}/:")
    print()
    for obj in sorted({o for o, _ in unresolved}):
        print(f"  {obj}")
        for line in scan.head([s for o, s in unresolved if o == obj], 6, "      "):
            print(line)
    print()
    print("Each of these calls a function that no library in its NEEDED closure defines, and")
    print("will fail with 'undefined symbol' when the call is first made. The usual cause is a")
    print("header from builder/deps.txt newer than the library packages/ ships: bump ours to")
    print("at least sid's (docs/guides/linking.md). A plugin that expects its host program to")
    print("provide the symbol is not a bug; add it to test/known-unresolved-symbols.txt.")
    return 1


def main(argv: list[str]) -> int:
    given, root = scan.tree_or_exit(argv)
    return scan.judged(check, given, root, given, ALLOWLIST)


if __name__ == "__main__":
    sys.exit(run(main, sys.argv[1:]))
