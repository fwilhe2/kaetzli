"""What a Debian snapshot bump changes in the toolchain:

    ./tools/snapshot-changes.py 20261027 20261104

A Markdown list for update-packages.yml's commit and pull request body. "The snapshot
moved by eight days" says nothing a reviewer can act on; "gcc 16.1 -> 16.2, Go 1.26 ->
1.27" says which packages a red build should be suspected of, and which numbers a size
or boot-time change may be blamed on.

Both dates' sid Packages index (amd64; one FROM line serves both arches, and sid builds
them from the same source) comes from snapshot.debian.org, the archive both Containerfiles
install from. Only TOOLS is reported, not every package the builder pulls in: the
compilers, the -dev packages our libraries are coupled to (CLAUDE.md, linking), and what
the assembly image lays the bytes out with. A metapackage (gcc, golang-go, python3) is
followed to the package it depends on, since its own version is the defaults package's.
"""

from __future__ import annotations

import lzma
import re
import sys
from dataclasses import dataclass

from kaetzli.updates.http import FetchError, Http

ARCHIVE = "http://snapshot.debian.org/archive/debian"


@dataclass(frozen=True)
class Tool:
    label: str
    package: str
    # A metapackage: the first Depends entry whose name fullmatches this is the real one.
    real: str = ""


TOOLS = (
    Tool("gcc", "gcc", r"gcc-\d+"),
    Tool("binutils", "binutils"),
    Tool("clang", "clang", r"clang-\d+"),
    Tool("Go", "golang-go", r"golang-[\d.]+-go"),
    Tool("rustc", "rustc"),
    Tool("glibc (headers, libc6)", "libc6"),
    Tool("Linux headers", "linux-libc-dev"),
    Tool("OpenSSL", "libssl-dev"),
    Tool("GLib", "libglib2.0-dev"),
    Tool("pahole", "pahole"),
    Tool("meson", "meson"),
    Tool("cmake", "cmake"),
    Tool("python3", "python3", r"python3\.\d+"),
    Tool("perl", "perl"),
    Tool("erofs-utils", "erofs-utils"),
    Tool("systemd-ukify", "systemd-ukify"),
    Tool("OVMF", "ovmf"),
)

Index = dict[str, dict[str, str]]


def url(date: str) -> str:
    return f"{ARCHIVE}/{date}T000000Z/dists/sid/main/binary-amd64/Packages.xz"


def parse(text: str) -> Index:
    """A Packages file: name -> its one-line fields (continuations dropped)."""
    index: Index = {}
    for stanza in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in stanza.splitlines():
            if line[:1] in (" ", "\t") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            fields[key] = value.strip()
        if "Package" in fields:
            index[fields["Package"]] = fields
    return index


def resolve(tool: Tool, index: Index) -> tuple[str, str] | None:
    """(package, version) the tool is in this index, or None when it is not."""
    fields = index.get(tool.package)
    if fields is None:
        return None
    if tool.real:
        for dep in fields.get("Depends", "").split(","):
            name = dep.split("|")[0].split()[0] if dep.strip() else ""
            if re.fullmatch(tool.real, name) and name in index:
                return name, index[name].get("Version", "?")
    return tool.package, fields.get("Version", "?")


def _show(r: tuple[str, str] | None, with_name: bool) -> str:
    if r is None:
        return "absent"
    return f"`{r[0]}` `{r[1]}`" if with_name else f"`{r[1]}`"


def report(old: Index, new: Index, tools=TOOLS) -> str:
    changed, same = [], []
    for t in tools:
        a, b = resolve(t, old), resolve(t, new)
        if a == b:
            if a is not None:
                same.append(f"{t.label} `{a[1]}`")
            continue
        renamed = a is not None and b is not None and a[0] != b[0]
        line = f"- {t.label}: {_show(a, renamed)} -> {_show(b, renamed)}"
        if not renamed and t.real and (a or b)[0] != t.package:
            line += f" ({(a or b)[0]})"
        changed.append(line)
    out = "\n".join(changed) if changed else "No change in the toolchain."
    if same:
        out += "\n\nUnchanged: " + ", ".join(same) + "."
    return out + "\n"


def run(argv: list[str], http, tools=TOOLS) -> int:
    if len(argv) != 2 or not all(re.fullmatch(r"[0-9]{8}", d) for d in argv):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        old, new = (parse(lzma.decompress(http.get_bytes(url(d))).decode()) for d in argv)
    except (FetchError, lzma.LZMAError, UnicodeDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("Toolchain in the builder and assembly images (sid, amd64):\n")
    print(report(old, new, tools), end="")
    return 0


def main(argv: list[str]) -> int:
    return run(argv, Http())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
