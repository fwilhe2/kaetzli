"""tools/snapshot-changes.py: what a Debian snapshot bump changes in the toolchain.

snapshot.debian.org is injected: each date's Packages index is a string here.
"""

import lzma

import pytest

from kaetzli.updates import snapshot_changes as sc
from kaetzli.updates.http import FetchError

OLD = """\
Package: gcc
Source: gcc-defaults (1.233)
Version: 4:16.1.0-3
Depends: cpp (= 4:16.1.0-3), gcc-16 (>= 16.1.0-3~), gcc-x86-64-linux-gnu (= 4:16.1.0-3)

Package: gcc-16
Source: gcc-16
Version: 16.1.0-3

Package: golang-go
Source: golang-defaults
Version: 2:1.26~1
Depends: golang-1.26-go, golang-src (>= 2:1.26~1)

Package: golang-1.26-go
Version: 1.26.4-1

Package: rustc
Version: 1.97.0+dfsg1-1
Depends: libc6 (>= 2.34)

Package: libc6
Source: glibc
Version: 2.43-7
Description: GNU C Library
 a continuation line: with a colon

Package: perl
Version: 5.42.3-1
"""

NEW = """\
Package: gcc
Source: gcc-defaults (1.234)
Version: 4:16.2.0-1
Depends: cpp (= 4:16.2.0-1), gcc-16 (>= 16.2.0-1~)

Package: gcc-16
Source: gcc-16
Version: 16.2.0-1

Package: golang-go
Source: golang-defaults
Version: 2:1.27~1
Depends: golang-1.27-go, golang-src (>= 2:1.27~1)

Package: golang-1.27-go
Version: 1.27.0-1

Package: rustc
Version: 1.97.1+dfsg1-1

Package: libc6
Source: glibc
Version: 2.43-7

Package: perl
Version: 5.42.3-1
"""

TOOLS = (
    sc.Tool("gcc", "gcc", r"gcc-\d+"),
    sc.Tool("Go", "golang-go", r"golang-[\d.]+-go"),
    sc.Tool("rustc", "rustc"),
    sc.Tool("glibc", "libc6"),
    sc.Tool("perl", "perl"),
    sc.Tool("cmake", "cmake"),
)


def test_parse_reads_stanzas_and_skips_continuations():
    idx = sc.parse(OLD)
    assert idx["libc6"]["Version"] == "2.43-7"
    assert idx["libc6"]["Description"] == "GNU C Library"
    assert set(idx) == {"gcc", "gcc-16", "golang-go", "golang-1.26-go", "rustc", "libc6", "perl"}


def test_resolve_follows_a_defaults_package_to_the_real_one():
    idx = sc.parse(OLD)
    assert sc.resolve(TOOLS[0], idx) == ("gcc-16", "16.1.0-3")
    assert sc.resolve(TOOLS[1], idx) == ("golang-1.26-go", "1.26.4-1")
    assert sc.resolve(TOOLS[2], idx) == ("rustc", "1.97.0+dfsg1-1")
    assert sc.resolve(TOOLS[5], idx) is None


def test_report():
    out = sc.report(sc.parse(OLD), sc.parse(NEW), TOOLS)
    assert out == (
        "- gcc: `16.1.0-3` -> `16.2.0-1` (gcc-16)\n"
        "- Go: `golang-1.26-go` `1.26.4-1` -> `golang-1.27-go` `1.27.0-1`\n"
        "- rustc: `1.97.0+dfsg1-1` -> `1.97.1+dfsg1-1`\n"
        "\n"
        "Unchanged: glibc `2.43-7`, perl `5.42.3-1`.\n"
    )


def test_report_nothing_changed():
    idx = sc.parse(OLD)
    out = sc.report(idx, idx, TOOLS[3:5])
    assert out == "No change in the toolchain.\n\nUnchanged: glibc `2.43-7`, perl `5.42.3-1`.\n"


def test_report_a_package_that_appears_or_goes():
    old = sc.parse("Package: cmake\nVersion: 4.4.5-1\n")
    out = sc.report(old, {}, TOOLS[5:])
    assert out == "- cmake: `4.4.5-1` -> absent\n"
    out = sc.report({}, old, TOOLS[5:])
    assert out == "- cmake: absent -> `4.4.5-1`\n"


class Archive:
    def __init__(self, by_date):
        self.by_date = by_date
        self.urls = []

    def get_bytes(self, url):
        self.urls.append(url)
        for date, text in self.by_date.items():
            if f"/{date}T000000Z/" in url:
                return lzma.compress(text.encode())
        raise FetchError(f"{url}: 404")


def test_main(capsys):
    archive = Archive({"20261027": OLD, "20261104": NEW})
    assert sc.run(["20261027", "20261104"], archive, TOOLS) == 0
    out, err = capsys.readouterr()
    assert out.startswith("Toolchain in the builder and assembly images (sid, amd64):\n\n- gcc:")
    assert err == ""
    assert archive.urls[0] == (
        "http://snapshot.debian.org/archive/debian/20261027T000000Z"
        "/dists/sid/main/binary-amd64/Packages.xz"
    )


def test_main_unreachable_archive_is_an_error(capsys):
    assert sc.run(["20261027", "20261104"], Archive({}), TOOLS) == 1
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("error: ")


@pytest.mark.parametrize("argv", [[], ["20261027"], ["2026-10-27", "20261104"], ["a", "b", "c"]])
def test_usage(argv, capsys):
    assert sc.run(argv, Archive({}), TOOLS) == 2
