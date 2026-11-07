"""kaetzli.rebuild: a release rebuilt from its commit, compared asset by asset with what
was published. The build and the download are injected; the mapping from a local
output/ to release names is release.collect's own."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from kaetzli import rebuild

NAME = "20261107.094545-751"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tree(root: Path) -> Path:
    """A worktree after tools/build-image.sh: output/ and the kernel's staging."""
    out = root / "output"
    (out / "extensions").mkdir(parents=True)
    (out / "confexts").mkdir()
    (out / "rootfs.erofs").write_bytes(b"erofs")
    (out / "rootfs.cmdline").write_bytes(b"cmdline")
    (out / "extensions" / "podman.raw").write_bytes(b"podman")
    (out / "extensions" / "podman.dm-verity").write_bytes(b"podman-verity")
    (out / "sbom-erofs.json").write_text("{}")
    (out / "sbom-oci.json").write_text("{}")
    (out / "sbom-ext-podman.json").write_text("{}")
    boot = root / "staging" / "kernel" / "rootfs" / "boot"
    boot.mkdir(parents=True)
    (boot / "bzImage").write_bytes(b"kernel")
    return root


def published(**override: bytes) -> str:
    files = {
        "rootfs-amd64.erofs": b"erofs",
        "rootfs-amd64.cmdline": b"cmdline",
        "kernel-amd64": b"kernel",
        "podman-amd64.sysext.raw": b"podman",
        "podman-amd64.sysext.dm-verity": b"podman-verity",
        "sbom-amd64.json": b"{}",
        "sbom-oci-amd64.json": b"{}",
        "sbom-ext-podman-amd64.json": b"{}",
        "cve-amd64.ods": b"a scan",
        "rootfs-arm64.erofs": b"other arch",
        "kaetzli-source.tar": b"source",
    } | {k.replace("_", "-").replace("-dot-", "."): v for k, v in override.items()}
    return "".join(f"{sha(v)}  {k}\n" for k, v in files.items())


def test_every_rebuilt_asset_is_compared_by_its_release_name(tmp_path):
    rows = rebuild.compare(tree(tmp_path), "amd64", ["podman"], rebuild.parse_sums(published()))
    by_name = {r.name: r.status for r in rows}
    assert by_name["rootfs-amd64.erofs"] == "same"
    assert by_name["kernel-amd64"] == "same"
    assert by_name["podman-amd64.sysext.raw"] == "same"
    assert by_name["sbom-ext-podman-amd64.json"] == "same"


def test_a_different_byte_is_named(tmp_path):
    sums = rebuild.parse_sums(published(**{"rootfs-amd64-dot-erofs": b"tampered"}))
    rows = rebuild.compare(tree(tmp_path), "amd64", ["podman"], sums)
    assert {r.name: r.status for r in rows}["rootfs-amd64.erofs"] == "DIFFERENT"


def test_what_a_rebuild_does_not_make_is_listed_not_judged(tmp_path):
    rows = rebuild.compare(tree(tmp_path), "amd64", ["podman"], rebuild.parse_sums(published()))
    by_name = {r.name: r.status for r in rows}
    assert by_name["cve-amd64.ods"] == "not rebuilt"  # a scan, not build output
    assert by_name["kaetzli-source.tar"] == "not rebuilt"
    assert "rootfs-arm64.erofs" not in by_name  # the other arch is not this rebuild's


def test_an_asset_the_release_lacks_is_a_difference(tmp_path):
    sums = rebuild.parse_sums(published())
    del sums["podman-amd64.sysext.dm-verity"]
    rows = rebuild.compare(tree(tmp_path), "amd64", ["podman"], sums)
    assert {r.name: r.status for r in rows}["podman-amd64.sysext.dm-verity"] == "not in the release"


def test_a_missing_local_output_is_a_difference(tmp_path):
    t = tree(tmp_path)
    (t / "output" / "rootfs.cmdline").unlink()
    rows = rebuild.compare(t, "amd64", ["podman"], rebuild.parse_sums(published()))
    assert {r.name: r.status for r in rows}["rootfs-amd64.cmdline"] == "not rebuilt"
    assert not rebuild.verdict(rows)


def test_the_verdict_is_every_rebuilt_asset_the_same(tmp_path):
    t = tree(tmp_path)
    assert rebuild.verdict(rebuild.compare(t, "amd64", ["podman"], rebuild.parse_sums(published())))
    bad = rebuild.parse_sums(published(**{"kernel-amd64": b"x"}))
    assert not rebuild.verdict(rebuild.compare(t, "amd64", ["podman"], bad))


def test_parse_sums_refuses_a_path(tmp_path):
    with pytest.raises(ValueError):
        rebuild.parse_sums(f"{'0' * 64}  ../etc/passwd\n")


def test_the_kernel_tar_holds_what_release_collect_reads(tmp_path):
    t = tree(tmp_path)
    data = rebuild.kernel_tar(t)
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        assert tf.extractfile("rootfs/boot/bzImage").read() == b"kernel"


# --- the run ----------------------------------------------------------------------------


def test_run_builds_the_commit_in_a_worktree_as_ci_assembled_it(tmp_path, capsys):
    calls = []

    def build(worktree: Path, env: dict) -> int:
        calls.append(env)
        tree(worktree)
        return 0

    rc = rebuild.run(
        NAME,
        "amd64",
        tmp_path / "rebuild",
        sha="a" * 40,
        extensions=["podman"],
        build=build,
        worktree=lambda path, sha: path.mkdir(parents=True),
        fetch=lambda url: published().encode(),
    )
    assert rc == 0
    assert calls == [{"KAETZLI_VERSION": NAME, "KAETZLI_BUILD": "a" * 40}]
    out = capsys.readouterr().out
    assert "reproduced" in out and "rootfs-amd64.erofs" in out


def test_run_fails_naming_what_differs(tmp_path, capsys):
    rc = rebuild.run(
        NAME,
        "amd64",
        tmp_path / "rebuild",
        sha="a" * 40,
        extensions=["podman"],
        build=lambda w, env: tree(w) and 0,
        worktree=lambda path, sha: path.mkdir(parents=True),
        fetch=lambda url: published(**{"kernel-amd64": b"x"}).encode(),
    )
    assert rc == 1
    assert "DIFFERENT" in capsys.readouterr().out


def test_a_failed_build_is_a_failure_without_comparing(tmp_path, capsys):
    rc = rebuild.run(
        NAME,
        "amd64",
        tmp_path / "rebuild",
        sha="a" * 40,
        extensions=[],
        build=lambda w, env: 2,
        worktree=lambda path, sha: path.mkdir(parents=True),
        fetch=lambda url: pytest.fail("nothing to compare"),
    )
    assert rc == 1
    assert "build failed" in capsys.readouterr().err
