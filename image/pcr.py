#!/usr/bin/env python3
"""The PCR 11 value a unified kernel image produces when systemd's stub boots it:

    pcr.py UKI > kaetzli.pcr.json

Under a TPM the stub measures each of the UKI's sections that it knows (.linux, .osrel,
.cmdline, …) into PCR 11, in a fixed order: the section's name with its NUL, then its
contents, each as one SHA-256 extend. Nothing else in this image extends PCR 11 (the
userspace phase measurements, systemd-pcrphase, need tpm2-tss, which is not built), so
the value a booted guest reports at /sys/class/tpm/tpm0/pcr-sha256/11 is exactly this
one, and a different kernel, command line (so root hash) or os-release gives a different
one. test/secureboot.py boots the UKI under a vTPM and compares.

The order and the rule for what is measured are systemd's (src/fundamental/uki.h,
src/boot/stub.c measure_sections): .pcrsig is skipped, since it signs the result. The
output is shaped like `systemd-measure calculate --json`: {"sha256": [{"pcr": 11,
"hash": …}]}. Run by image/build-rootfs.sh in the assembly container, so stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

PCR = 11

# systemd's canonical order (UnifiedSection); PLEASE DO NOT REORDER, it says.
SECTIONS = (
    ".linux", ".osrel", ".cmdline", ".initrd", ".ucode", ".splash", ".dtb", ".uname",
    ".sbat", ".pcrsig", ".pcrpkey", ".profile", ".dtbauto", ".hwids", ".efifw",
)  # fmt: skip
NOT_MEASURED = {".pcrsig"}


class NotAPE(ValueError):
    pass


def sections(pe: bytes) -> dict[str, bytes]:
    """Each section's contents as it is in memory: VirtualSize bytes, the file's raw data
    zero-filled past SizeOfRawData (the stub measures memory, not the file)."""
    if pe[:2] != b"MZ" or len(pe) < 0x40:
        raise NotAPE("no MZ header")
    (pe_off,) = struct.unpack_from("<I", pe, 0x3C)
    if pe[pe_off : pe_off + 4] != b"PE\0\0":
        raise NotAPE("no PE signature")
    count, optional = struct.unpack_from("<H12xH", pe, pe_off + 6)
    table = pe_off + 24 + optional
    out: dict[str, bytes] = {}
    for i in range(count):
        name, vsize, _va, raw_size, raw_off = struct.unpack_from("<8sIIII", pe, table + 40 * i)
        key = name.rstrip(b"\0").decode("ascii", "replace")
        if key in out:
            raise NotAPE(f"section {key} twice")
        data = pe[raw_off : raw_off + min(vsize, raw_size)]
        out[key] = data + b"\0" * (vsize - len(data))
    return out


def pcr11(found: dict[str, bytes]) -> str:
    """The SHA-256 bank's PCR 11 after the stub's measurements, from zero."""
    pcr = b"\0" * 32
    for name in SECTIONS:
        if name in NOT_MEASURED or name not in found:
            continue
        for event in (name.encode() + b"\0", found[name]):
            pcr = hashlib.sha256(pcr + hashlib.sha256(event).digest()).digest()
    return pcr.hex()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: pcr.py UKI", file=sys.stderr)
        return 2
    try:
        found = sections(Path(argv[0]).read_bytes())
    except (OSError, NotAPE, struct.error) as e:
        print(f"error: {argv[0]}: {e}", file=sys.stderr)
        return 1
    if ".linux" not in found:
        print(f"error: {argv[0]} has no .linux section: not a UKI", file=sys.stderr)
        return 1
    print(json.dumps({"sha256": [{"pcr": PCR, "hash": pcr11(found)}]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
