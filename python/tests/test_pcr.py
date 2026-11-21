"""image/pcr.py, the PCR 11 prediction the assembly container runs on its own python3.

The expected hashes are what `systemd-measure calculate --bank=sha256 --phase= --json=short`
(systemd 262) printed for the same section contents, so they are systemd's arithmetic,
not this file's.
"""

import importlib.util
import json
import struct
import subprocess
import sys

import pytest

from kaetzli import repo

SCRIPT = repo.ROOT / "image" / "pcr.py"
spec = importlib.util.spec_from_file_location("pcr", SCRIPT)
pcr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pcr)

LINUX = bytes(range(256)) * 20
SECTIONS = {
    ".linux": LINUX,
    ".osrel": b"ID=kaetzli\n",
    ".cmdline": b"root=/dev/dm-0 ro\n",
    ".uname": b"7.3.5\n",
    ".sbat": b"sbat,1\n",
}
# systemd-measure calculate --linux --osrel --cmdline --uname --sbat
ALL = "ff2536985d98550fa41fd232038affe9d722ec916cbc11d5e3a21b1b7b7e72f4"
# systemd-measure calculate --linux
LINUX_ONLY = "6b8c435b2aef4dcfaa8a0cd264d8b9e9d3c4627d27ba2af68266dd15525afe09"


def pe(sections: list[tuple[str, bytes, int]]) -> bytes:
    """A PE image with these (name, raw data, VirtualSize) sections and nothing else
    the parser reads: MZ, e_lfanew, the signature, the COFF header, an empty optional
    header of 16 bytes, the section table, then the data."""
    pe_off, optional = 0x40, 16
    table = pe_off + 24 + optional
    data_at = table + 40 * len(sections)
    head = bytearray(data_at)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, pe_off)
    head[pe_off : pe_off + 4] = b"PE\0\0"
    struct.pack_into("<H12xH", head, pe_off + 6, len(sections), optional)
    body = b""
    for i, (name, raw, vsize) in enumerate(sections):
        off = data_at + len(body)
        struct.pack_into(
            "<8sIIII", head, table + 40 * i, name.encode(), vsize, 0x1000 * (i + 1), len(raw), off
        )
        body += raw
    return bytes(head) + body


def test_systemd_measures_the_same():
    assert pcr.pcr11(SECTIONS) == ALL
    assert pcr.pcr11({".linux": LINUX}) == LINUX_ONLY


def test_order_is_systemds_not_the_files():
    shuffled = dict(reversed(list(SECTIONS.items())))
    assert pcr.pcr11(shuffled) == ALL


def test_pcrsig_and_the_stubs_own_sections_are_not_measured():
    extra = {**SECTIONS, ".pcrsig": b"{}", ".text": b"code", ".reloc": b"x"}
    assert pcr.pcr11(extra) == ALL


def test_sections_from_a_pe_are_what_memory_holds():
    image = pe(
        [(".text", b"code", 4), (".linux", LINUX[:-8], len(LINUX)), (".osrel", b"ID=x\n", 3)]
    )
    found = pcr.sections(image)
    # VirtualSize past the raw data is zero-filled; short of it, cut.
    assert found[".linux"] == LINUX[:-8] + b"\0" * 8
    assert found[".osrel"] == b"ID="
    assert set(found) == {".text", ".linux", ".osrel"}


def test_a_pe_end_to_end(tmp_path):
    image = pe([(n, d, len(d)) for n, d in SECTIONS.items()])
    (tmp_path / "uki.efi").write_bytes(image)
    r = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), str(tmp_path / "uki.efi")],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"sha256": [{"pcr": 11, "hash": ALL}]}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not a pe", "no MZ header"),
        (pe([(".text", b"code", 4)]), "has no .linux section"),
        (pe([(".linux", b"a", 1), (".linux", b"b", 1)]), "section .linux twice"),
    ],
)
def test_what_is_not_a_uki(tmp_path, capsys, data, message):
    (tmp_path / "x").write_bytes(data)
    assert pcr.main([str(tmp_path / "x")]) == 1
    assert message in capsys.readouterr().err


def test_usage(capsys):
    assert pcr.main([]) == 2
    assert "usage: pcr.py UKI" in capsys.readouterr().err
