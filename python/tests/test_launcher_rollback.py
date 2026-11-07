"""kaetzli.boot.launcher_rollback's pure parts: the broken snapshot and the cache layout
the launcher reads (go/internal/snapshot)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kaetzli.boot import launcher_rollback as lr

HASH = "ab" * 32
TABLE = f"0 8 verity 1 /dev/vda /dev/vda 4096 4096 1 1 sha256 {HASH} {'cd' * 32}"
CMDLINE = f'root=/dev/dm-0 dm-mod.create="root,,,ro,{TABLE}"\n'


def test_break_changes_only_the_root_hash():
    broken = lr.break_root_hash(CMDLINE)
    assert broken != CMDLINE
    assert broken.replace(f"sha256 {'0' + HASH[1:]}", f"sha256 {HASH}") == CMDLINE


def test_break_refuses_a_command_line_without_a_root_hash():
    with pytest.raises(ValueError):
        lr.break_root_hash("root=/dev/vda\n")


def test_a_cached_snapshot_is_laid_out_as_the_launcher_reads_it(tmp_path):
    for f, data in (
        ("rootfs.erofs", b"r"),
        ("rootfs.cmdline", CMDLINE.encode()),
        ("bzImage", b"k"),
    ):
        (tmp_path / f).write_bytes(data)
    d = lr.cache_snapshot(tmp_path / "cache", "20260101.000000-1", "amd64",
                          tmp_path / "rootfs.erofs", tmp_path / "bzImage", CMDLINE)  # fmt: skip
    assert d == tmp_path / "cache" / "20260101.000000-1-amd64"
    assert sorted(p.name for p in d.iterdir()) == [
        "SHA256SUMS", "bzImage", "rootfs.cmdline", "rootfs.erofs", "snapshot",
    ]  # fmt: skip
    assert (d / "snapshot").read_text() == "20260101.000000-1\n"
    assert Path(d / "rootfs.cmdline").read_text() == CMDLINE
