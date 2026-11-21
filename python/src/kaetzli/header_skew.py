"""How far the builder's development headers are from the libraries we ship:

    ./tools/header-skew.py

Every library in packages/ is compiled against twice over, in a sense: packages that link
it compile against *Debian's* header for it (the -dev section of builder/deps.txt, since
the CI sysroot is glibc alone), and run against *ours*, found by SONAME. That works while
the two stay close, and docs/guides/linking.md is the long version of why.

This is the report that says how close. For each -dev package in that section: sid's
version, ours, and which is newer. Ours newer is the safe direction — a library is
backwards compatible with headers older than itself. sid's newer is the one to look at: a
binary built against that header may call a function our library does not have yet.
Whether one actually does is test/check-symbol-resolution.py's question (and, for a
library with versioned symbols, test/check-symbol-versions.py's); this only says where to
expect it. It never fails: a skew is a fact about two release schedules, not a bug.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from debian.debian_support import Version

from kaetzli import repo

# Debian's binary package -> the package under packages/ that ships the library. Written
# out rather than derived: Debian's source names (libcap2, glib2.0, xz-utils) are not
# ours, and a lookup table nobody has to reverse-engineer is the point of the report.
OURS = {
    "libacl1-dev": "acl",
    "libattr1-dev": "attr",
    "libblkid-dev": "util-linux",
    "libcap-dev": "libcap",
    "libcrypt-dev": "libxcrypt",
    "libdbus-1-dev": "dbus",
    "libelf-dev": "elfutils",
    "libexpat-dev": "expat",
    "libfdisk-dev": "util-linux",
    "libjansson-dev": "jansson",
    "libglib2.0-dev": "glib",
    "libjson-c-dev": "json-c",
    "liblzma-dev": "xz",
    "libmnl-dev": "libmnl",
    "libmount-dev": "util-linux",
    "libncurses-dev": "ncurses",
    "libnftnl-dev": "libnftnl",
    "libpam0g-dev": "pam",
    "libpcre2-dev": "pcre2",
    "libseccomp-dev": "libseccomp",
    "libssl-dev": "openssl",
    "libsystemd-dev": "systemd",
    "libzstd-dev": "zstd",
    "uuid-dev": "util-linux",
    "zlib1g-dev": "zlib",
}
# In the section, but with nothing of ours behind it: glib's build needs libffi for
# GObject, and GObject is not shipped (packages/glib/build.sh).
BUILD_ONLY = {"libffi-dev"}

DEPS = "builder/deps.txt"
_START = "# Development headers, only for libraries"
_END = "# The one exception to"

# Each -dev package's version in the builder, or nothing: a virtual one (libexpat-dev)
# through the package that provides it. dpkg-query is in the builder image because it is
# the image those headers are in, and the host may not have dpkg.
_QUERY = r"""
for dev in "$@"; do
    v=$(dpkg-query -W -f='${Version}' "$dev" 2>/dev/null || true)
    if [ -z "$v" ]; then
        p=$(apt-cache showpkg "$dev" 2>/dev/null |
            awk '/^Reverse Provides:/ { getline; print $1; exit }')
        [ -z "$p" ] || v=$(dpkg-query -W -f='${Version}' "$p" 2>/dev/null || true)
    fi
    printf '%s\t%s\n' "$dev" "$v"
done
"""

Entry = tuple[str, str, str]


class Unknown(ValueError):
    pass


def dev_section(text: str) -> list[str]:
    """The packages in deps.txt's -dev section, from its header to the gcc paragraph
    after it. If either marker moves, the result is empty rather than the wrong lines."""
    devs: list[str] = []
    on = False
    for line in text.splitlines():
        if line.startswith(_START):
            on = True
        elif line.startswith(_END):
            return devs if on else []
        elif on and line[:1].isalnum() and line[:1] == line[:1].lower():
            devs.append(line.split()[0])
    return devs


def entries(devs: list[str], versions: dict[str, str]) -> list[Entry]:
    """(dev, our package, its version) for each, ("-", "-") for a build-only one."""
    out: list[Entry] = []
    for dev in devs:
        if dev in BUILD_ONLY:
            out.append((dev, "-", "-"))
        elif dev in OURS:
            out.append((dev, OURS[dev], versions[OURS[dev]]))
        else:
            raise Unknown(dev)
    return out


def sid_versions(devs: list[str], image: str, run: Callable = subprocess.run) -> dict:
    out = run(
        ["podman", "run", "--rm", "--network=none", image, "bash", "-c", _QUERY, "bash", *devs],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    sid: dict[str, str | None] = {}
    for line in out.splitlines():
        dev, _, version = line.partition("\t")
        sid[dev] = version or None
    return sid


def upstream(full: str | None) -> str:
    """The upstream version in a Debian one, "?" for none.

    The epoch and the Debian revision go (1:2.42.4-1 is upstream 2.42.4), and so does a
    +dfsg-style repack suffix, which says nothing about the API. "+really" is how Debian
    moves a version *backwards* without breaking its own ordering — zlib's
    1.3.dfsg+really1.3.2 is upstream 1.3.2 — so what follows it is the version.
    """
    if full is None:
        return "?"
    up = full.split(":", 1)[1] if ":" in full else full
    up = up.rsplit("-", 1)[0]
    if "+really" in up:
        up = up.rsplit("+really", 1)[1]
    return up.split("+dfsg", 1)[0].split("+ds", 1)[0]


def _compare(a: str, b: str) -> int | None:
    try:
        va, vb = Version(a), Version(b)
    except ValueError:
        return None
    return (va > vb) - (va < vb)


def state(mine: str, up: str) -> tuple[str, str]:
    """Ours against sid's, in dpkg's order. A +YYYYMMDD suffix on our exact release
    (ncurses, libxcrypt) is sid shipping upstream's patches since that release: newer,
    but by fixes rather than by a release, so it gets a label of its own instead of being
    lumped in with a whole release ahead."""
    if up == "?":
        return "?", "not installed in the builder"
    order = _compare(mine, up)
    if order == 1:
        return "newer", ""
    if order == 0:
        return "same", ""
    if up.split("+", 1)[0] == mine:
        return "patched", "sid adds upstream fixes since this release"
    return "OLDER", "<- a whole release behind the sid headers"


def _row(*cols: str) -> str:
    return "{:<16} {:<20} {:<16} {:<8} {}".format(*cols)


def report(ours: list[Entry], sid: dict[str, str | None]) -> list[str]:
    lines = [_row("builder -dev", "ours", "sid", "ours is", "")]
    older = 0
    for dev, pkg, mine in ours:
        up = upstream(sid.get(dev))
        if pkg == "-":
            lines.append(_row(dev, "-", up, "-", "build only: nothing of ours behind it"))
            continue
        st, note = state(mine, up)
        older += st == "OLDER"
        lines.append(_row(dev, f"{pkg} {mine}", up, st, note))
    lines.append("")
    if older:
        lines += [
            f"{older} header(s) newer than the library we ship. Not an error by itself:",
            "test/check-symbol-resolution.py says whether any binary actually uses what is",
            "missing. See docs/guides/linking.md.",
        ]
    else:
        lines.append(
            "Every library we ship is at least as new as the header it is compiled against."
        )
    return lines


def main(argv: list[str]) -> int:
    if argv:
        print("usage: tools/header-skew.py", file=sys.stderr)
        return 2
    repo.enter_root()
    devs = dev_section(Path(DEPS).read_text(encoding="utf-8"))
    if not devs:
        print(f"error: no -dev section found in {DEPS}", file=sys.stderr)
        return 1
    try:
        ours = entries(devs, repo.env_get_all("VERSION"))
    except Unknown as e:
        print(
            f"error: {e} is in {DEPS}'s -dev section but not in"
            " python/src/kaetzli/header_skew.py's table",
            file=sys.stderr,
        )
        return 1
    image = subprocess.run(
        ["./tools/image-tags.sh", "builder"], check=True, capture_output=True, text=True
    ).stdout.strip()
    for line in report(ours, sid_versions(devs, image)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
