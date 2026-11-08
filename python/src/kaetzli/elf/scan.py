"""What the three library checks read from a tree: each ELF file's dynamic section,
version tables and dynamic symbols, and the tree's symlinks.

    deps        check-rootfs-deps.py: is every NEEDED library in the tree?
    versions    check-symbol-versions.py: does it define the versions a binary asks for?
    resolution  check-symbol-resolution.py: does the NEEDED closure define every symbol?

pyelftools reads the tables themselves, so there is no readelf text to misparse. The bash
versions split readelf's rows into columns, and on arm64 a `[VARIANT_PCS]` beside the
visibility moved every column after it by one: libmvec's vector functions were read as
definitions of their section number, and an undefined one would have been a definition
of `UND`.

Every regular file is opened (`find -type f`: not a symlink, which is counted separately);
one that is not ELF is skipped. One that starts with the ELF magic and that pyelftools
cannot read is an error (Unreadable), naming each: the bash versions sent readelf's errors
to /dev/null, so a truncated or corrupt binary was skipped, and a check that skips what it
cannot judge passes. The files are read in parallel, one process per CPU.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile
from elftools.elf.gnuversions import GNUVerDefSection, GNUVerNeedSection
from elftools.elf.sections import SymbolTableSection

_MAGIC = b"\x7fELF"


class Unreadable(Exception):
    """Files that start with the ELF magic but cannot be read, as (path, reason)."""

    def __init__(self, files: list[tuple[str, str]]):
        super().__init__(", ".join(rel for rel, _ in files))
        self.files = files

    def report(self, given: str) -> str:
        """What every check prints for them before failing."""
        lines = [f"UNREADABLE ELF files in {given}/:", ""]
        lines += [f"  {rel}: {reason}" for rel, reason in self.files]
        lines += [
            "",
            "Each starts with the ELF magic but cannot be parsed, so nothing about it was",
            "checked: a truncated copy, a corrupt build, or an ELF feature pyelftools does",
            "not know. Passing it would pass what was never judged.",
        ]
        return "\n".join(lines)


def reason(e: Exception) -> str:
    """One line for why a file could not be read."""
    return f"{type(e).__name__}: {e}".splitlines()[0]


@dataclass
class Elf:
    path: str  # relative to the tree's root
    soname: str | None  # DT_SONAME, if it has one
    needed: list[str] = field(default_factory=list)
    # The versions it defines (the first name of each definition, the BASE one being its
    # SONAME), and the ones it needs, as (NEEDED name, version).
    defines: list[str] = field(default_factory=list)
    needs: list[tuple[str, str]] = field(default_factory=list)
    has_dynsym: bool = False
    defined: frozenset[str] = frozenset()
    undefined: list[str] = field(default_factory=list)  # not weak, in table order

    @property
    def name(self) -> str:
        """What the loader can know it by: its SONAME, or its file name when it has none."""
        return self.soname or self.path.rsplit("/", 1)[-1]


def read(root: Path, rel: str, symbols: bool = False) -> Elf | None:
    """One file, or None when it is not ELF; Unreadable when it is and cannot be read."""
    try:
        with open(root / rel, "rb") as f:
            if f.read(4) != _MAGIC:
                return None
            f.seek(0)
            return _read(ELFFile(f), rel, symbols)
    except Exception as e:
        raise Unreadable([(rel, reason(e))]) from e


def _read(elf: ELFFile, rel: str, symbols: bool) -> Elf:
    e = Elf(path=rel, soname=None)
    for section in elf.iter_sections():
        if isinstance(section, DynamicSection):
            for tag in section.iter_tags():
                if tag.entry.d_tag == "DT_NEEDED":
                    e.needed.append(tag.needed)
                elif tag.entry.d_tag == "DT_SONAME":
                    e.soname = tag.soname
        elif isinstance(section, GNUVerDefSection):
            for _verdef, aux in section.iter_versions():
                first = next(iter(aux), None)
                if first is not None:
                    e.defines.append(first.name)
        elif isinstance(section, GNUVerNeedSection):
            for verneed, aux in section.iter_versions():
                e.needs.extend((verneed.name, a.name) for a in aux)
        elif symbols and isinstance(section, SymbolTableSection):
            if section["sh_type"] != "SHT_DYNSYM":
                continue
            e.has_dynsym = True
            defined = set()
            for sym in section.iter_symbols():
                if not sym.name:
                    continue
                if sym["st_shndx"] == "SHN_UNDEF":
                    if sym["st_info"]["bind"] != "STB_WEAK":
                        e.undefined.append(sym.name)
                else:
                    defined.add(sym.name)
            e.defined = frozenset(defined)
    return e


def regular_files(root: Path) -> list[str]:
    """Every regular file under root, relative to it, in byte order."""
    out = []
    for d, dirs, files in os.walk(root):
        dirs.sort()
        rel = os.path.relpath(d, root)
        for name in files:
            p = os.path.join(d, name)
            if not os.path.islink(p) and os.path.isfile(p):
                out.append(name if rel == "." else f"{rel}/{name}")
    return sorted(out)


def symlinks(root: Path) -> list[str]:
    """Every symlink under root, relative to it, in byte order."""
    out = []
    for d, dirs, files in os.walk(root):
        rel = os.path.relpath(d, root)
        for name in dirs + files:
            if os.path.islink(os.path.join(d, name)):
                out.append(name if rel == "." else f"{rel}/{name}")
    return sorted(out)


def _chunk(args: tuple[str, list[str], bool]) -> tuple[list[Elf], list[tuple[str, str]]]:
    root, rels, symbols = args
    elfs, unreadable = [], []
    for rel in rels:
        try:
            e = read(Path(root), rel, symbols)
        except Unreadable as u:
            unreadable += u.files
            continue
        if e is not None:
            elfs.append(e)
    return elfs, unreadable


def scan(root: Path, symbols: bool = False, jobs: int | None = None) -> list[Elf]:
    """Every ELF file under root, in byte order of its path. Unreadable, naming every one,
    when any file that starts like ELF cannot be read."""
    rels = regular_files(root)
    jobs = jobs or os.cpu_count() or 1
    if jobs == 1 or len(rels) < 64:
        parts = [_chunk((str(root), rels, symbols))]
    else:
        size = 40
        chunks = [(str(root), rels[i : i + size], symbols) for i in range(0, len(rels), size)]
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            parts = list(pool.map(_chunk, chunks))
    unreadable = [u for _, us in parts for u in us]
    if unreadable:
        raise Unreadable(unreadable)
    return [e for elfs, _ in parts for e in elfs]


def tree_or_exit(argv: list[str]) -> tuple[str, Path]:
    """The tree a check was given (rootfs by default), as said and as a path."""
    given = argv[0] if argv else "rootfs"
    if not Path(given).is_dir():
        print(f"error: no such directory: {given}", file=sys.stderr)
        sys.exit(1)
    return given, Path(given)


def judged(check: Callable[..., int], given: str, *args) -> int:
    """check(*args), or 1 with Unreadable's report: how every check here fails on a file
    it could not read rather than passing it."""
    try:
        return check(*args)
    except Unreadable as u:
        print(u.report(given))
        return 1


def head(items: list[str], n: int, prefix: str) -> list[str]:
    """The first n, then how many more: how every finding lists what it names."""
    out = [f"{prefix}{x}" for x in items[:n]]
    if len(items) > n:
        out.append(f"      ... and {len(items) - n} more")
    return out
