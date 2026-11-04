"""Check that every package's binaries carry the hardening builder/build-package.sh asks for.

    ./test/check-hardening.py [staging-dir] [allowlist]

The flags are set once, in build-package.sh, and reach a binary only if the package's
build system appends to CFLAGS and LDFLAGS rather than replacing them. One that replaces
them builds and runs exactly as before, just without the hardening, so the result is
checked on what was built: each package's own tree, staging/<pkg>/, base and extensions
alike, so a finding names the package to fix.

Judged per file, for every dynamically linked ELF:

    relro            a PT_GNU_RELRO segment                     -Wl,-z,relro
    bind-now         DF_BIND_NOW / DF_1_NOW, so all of RELRO    -Wl,-z,now
                     is read-only after start-up
    pie              an executable is ET_DYN                    -fPIE -pie (gcc's default)
    exec-stack       a PT_GNU_STACK without PF_X (any ELF)      -Wl,-z,noexecstack
    fortify          no unfortified printf-family import:       -D_FORTIFY_SOURCE=3 at -O2
                     with _FORTIFY_SOURCE >= 2 glibc's headers
                     turn every printf, fprintf, syslog, ...
                     into __*_chk, so a plain one is a
                     translation unit built without it (or -O0)

and per package, since a function with nothing to protect gets no canary:

    stack-protector  some file of the package calls             -fstack-protector-strong
                     __stack_chk_fail

A static binary (no PT_INTERP, no dynamic section; a CGO_ENABLED=0 Go program) is held to
exec-stack alone. Go and Rust compile their own code and are not held to the C compiler's
stack-protector and fortify; their linking is held to the rest.

test/known-unhardened.txt allowlists a package's property, with the reason beside it. A
line for a staged package that no longer has the finding fails too, so the list only
shrinks when something is fixed.

The control-flow marking (x86 IBT/SHSTK, arm64 BTI) is counted, not checked: a binary is
marked only when every object linked into it is, our glibc's start files included.
"""

from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import NoteSection, SymbolTableSection

from kaetzli import repo
from kaetzli.elf import scan
from kaetzli.report import run

ALLOWLIST = repo.ROOT / "test" / "known-unhardened.txt"
_SKIP = re.compile(r"^\s*(#|$)")

PROPERTIES = {
    "relro": "no PT_GNU_RELRO segment (-Wl,-z,relro)",
    "bind-now": "no BIND_NOW: the GOT stays writable (-Wl,-z,now)",
    "pie": "an executable that is not position-independent (-fPIE -pie)",
    "exec-stack": "an executable stack (-Wl,-z,noexecstack)",
    "fortify": "printf-family calls that are not __*_chk (-D_FORTIFY_SOURCE at -O2)",
    "stack-protector": "no file calls __stack_chk_fail (-fstack-protector-strong)",
}

# What _FORTIFY_SOURCE >= 2 rewrites to __*_chk unconditionally, whatever the compiler
# knows about object sizes: an import of one of these is a translation unit without it.
ALWAYS_FORTIFIED = frozenset(
    {
        "printf",
        "fprintf",
        "vprintf",
        "vfprintf",
        "dprintf",
        "vdprintf",
        "asprintf",
        "vasprintf",
        "syslog",
        "vsyslog",
        "obstack_printf",
        "obstack_vprintf",
    }
)

_DF_BIND_NOW = 0x8
_DF_1_NOW = 0x1
_PF_X = 0x1
_X86_IBT, _X86_SHSTK = 0x1, 0x2
_AARCH64_BTI = 0x1


@dataclass
class Binary:
    path: str  # relative to the package's staging tree
    static: bool
    executable: bool  # has an interpreter
    lang: str  # c, go or rust
    findings: list[str] = field(default_factory=list)
    unfortified: list[str] = field(default_factory=list)
    calls_ssp: bool = False
    cfi: bool | None = None  # control-flow marked; None where the arch has no such note


def examine(path: Path, rel: str) -> Binary | None:
    """One file's hardening, or None when it is not an executable or shared object."""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"\x7fELF":
                return None
            f.seek(0)
            return _examine(ELFFile(f), rel)
    except Exception:
        return None


def _examine(elf: ELFFile, rel: str) -> Binary | None:
    if elf["e_type"] not in ("ET_EXEC", "ET_DYN"):
        return None
    segments = {s["p_type"]: s for s in elf.iter_segments()}
    interp = "PT_INTERP" in segments
    dynamic = "PT_DYNAMIC" in segments
    b = Binary(path=rel, static=not interp and not dynamic, executable=interp, lang=_lang(elf))

    stack = segments.get("PT_GNU_STACK")
    if stack is None or stack["p_flags"] & _PF_X:
        b.findings.append("exec-stack")
    if b.static:
        return b

    if "PT_GNU_RELRO" not in segments:
        b.findings.append("relro")
    if interp and elf["e_type"] != "ET_DYN":
        b.findings.append("pie")

    now = False
    for section in elf.iter_sections():
        if isinstance(section, DynamicSection):
            for tag in section.iter_tags():
                t, v = tag.entry.d_tag, tag.entry.d_val
                if (
                    t == "DT_BIND_NOW"
                    or (t == "DT_FLAGS" and v & _DF_BIND_NOW)
                    or (t == "DT_FLAGS_1" and v & _DF_1_NOW)
                ):
                    now = True
        elif isinstance(section, SymbolTableSection) and section["sh_type"] == "SHT_DYNSYM":
            for sym in section.iter_symbols():
                if sym.name == "__stack_chk_fail":
                    b.calls_ssp = True  # imported, or defined by libc itself
                elif sym["st_shndx"] == "SHN_UNDEF" and sym.name in ALWAYS_FORTIFIED:
                    b.unfortified.append(sym.name)
        elif isinstance(section, NoteSection) and section.name == ".note.gnu.property":
            b.cfi = _cfi(section, elf["e_machine"])
    if not now:
        b.findings.append("bind-now")
    if b.unfortified and b.lang == "c":
        b.findings.append("fortify")
    return b


def _lang(elf: ELFFile) -> str:
    if elf.get_section_by_name(".go.buildinfo") or elf.get_section_by_name(".note.go.buildid"):
        return "go"
    comment = elf.get_section_by_name(".comment")
    if comment is not None and b"rustc" in comment.data():
        return "rust"
    return "c"


def _cfi(section: NoteSection, machine: str) -> bool | None:
    for note in section.iter_notes():
        if note["n_type"] != "NT_GNU_PROPERTY_TYPE_0":
            continue
        for prop in note["n_desc"]:
            kind, data = prop["pr_type"], prop["pr_data"]
            if kind == "GNU_PROPERTY_X86_FEATURE_1_AND" and isinstance(data, int):
                return data & (_X86_IBT | _X86_SHSTK) == (_X86_IBT | _X86_SHSTK)
            if kind == "GNU_PROPERTY_AARCH64_FEATURE_1_AND" and isinstance(data, int):
                return bool(data & _AARCH64_BTI)
    return False if machine in ("EM_X86_64", "EM_AARCH64") else None


def _chunk(args: tuple[str, str, list[str]]) -> list[tuple[str, Binary]]:
    staging, pkg, rels = args
    tree = Path(staging) / pkg
    return [(pkg, b) for rel in rels if (b := examine(tree / rel, rel)) is not None]


def staged(staging: Path) -> list[str]:
    """The packages in a staging directory: build.sh's .building-<pkg> is not one yet."""
    return sorted(p.name for p in staging.iterdir() if p.is_dir() and not p.name.startswith("."))


def scan_staging(staging: Path) -> dict[str, list[Binary]]:
    """Every package's ELF executables and shared objects, by package."""
    work = []
    for pkg in staged(staging):
        rels = scan.regular_files(staging / pkg)
        work += [(str(staging), pkg, rels[i : i + 40]) for i in range(0, len(rels), 40)]
    jobs = os.cpu_count() or 1
    if jobs == 1 or len(work) < 4:
        parts = list(map(_chunk, work))
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            parts = list(pool.map(_chunk, work))
    out: dict[str, list[Binary]] = defaultdict(list)
    for part in parts:
        for pkg, b in part:
            out[pkg].append(b)
    return dict(out)


def findings(binaries: list[Binary]) -> dict[str, list[str]]:
    """A package's findings: property -> the files that have it (empty for a package's own)."""
    out: dict[str, list[str]] = defaultdict(list)
    for b in binaries:
        for f in b.findings:
            out[f].append(b.path)
    c = [b for b in binaries if b.lang == "c" and not b.static]
    if c and not any(b.calls_ssp for b in c):
        out["stack-protector"] = []
    return dict(out)


def read_allowlist(path: Path) -> set[tuple[str, str]]:
    if not path.is_file():
        return set()
    out = set()
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if _SKIP.match(line):
            continue
        parts = line.split("#", 1)[0].split()
        if len(parts) != 2 or parts[1] not in PROPERTIES:
            print(
                f"error: {path}:{n}: expected '<package> <property>', property one of "
                + ", ".join(PROPERTIES),
                file=sys.stderr,
            )
            sys.exit(2)
        out.add((parts[0], parts[1]))
    return out


def check(staging: Path, given: str, allowlist: Path) -> int:
    allowed = read_allowlist(allowlist)
    packages = scan_staging(staging)
    total = sum(len(v) for v in packages.values())
    if total == 0:
        print(f"error: no ELF executable or shared object under {given}/: nothing was judged")
        return 1

    found = {
        (pkg, prop): files
        for pkg, bins in packages.items()
        for prop, files in findings(bins).items()
    }
    new = sorted(k for k in found if k not in allowed)
    known = sorted(k for k in found if k in allowed)
    present = set(staged(staging))
    stale = sorted(k for k in allowed if k[0] in present and k not in found)

    def report(keys: list[tuple[str, str]]) -> None:
        for pkg, prop in keys:
            print(f"  {pkg}: {prop}: {PROPERTIES[prop]}")
            for line in scan.head(sorted(found[(pkg, prop)]), 4, "      "):
                print(line)

    print(f"{total} ELF files in {len(packages)} packages under {given}/.")
    cfi = [b.cfi for bins in packages.values() for b in bins if b.cfi is not None]
    if cfi:
        print(f"Control-flow marked (IBT+SHSTK or BTI): {sum(cfi)} of {len(cfi)}.")
    print()

    if known:
        print(f"Known (listed in {allowlist.name}):")
        report(known)
        print()
    if stale:
        print(f"Lines in {allowlist.name} that no longer apply; delete them:")
        for pkg, prop in stale:
            print(f"  {pkg} {prop}")
        print()
    if new:
        print("NEW hardening findings:")
        report(new)
        print()
        print("builder/build-package.sh sets these flags for every package; a build system")
        print("that replaces CFLAGS or LDFLAGS rather than appending to them drops them in")
        print("silence. Pass the exported flags through (CLAUDE.md, 'The three things that")
        print("break silently'), or list the finding in known-unhardened.txt with the reason.")
    if new or stale:
        return 1
    print("Every package is hardened as builder/build-package.sh asks.")
    return 0


def main(argv: list[str]) -> int:
    given = argv[0] if argv else "staging"
    if not Path(given).is_dir():
        print(f"error: no such directory: {given}", file=sys.stderr)
        sys.exit(1)
    return check(Path(given), given, Path(argv[1]) if len(argv) > 1 else ALLOWLIST)


if __name__ == "__main__":
    sys.exit(run(main, sys.argv[1:]))
