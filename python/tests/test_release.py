"""tools/release.py: rolling snapshots of main as GitHub releases (docs/proposals/release.md)."""

import io
import json
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kaetzli import release

ARCHS = ("amd64", "arm64")
EXTS = {"podman": False, "kubernetes": True, "debug": False}  # name → has a confext


def test_a_name_is_recognised_and_nothing_else():
    assert release.is_snapshot("20261103.141205-42")
    assert not release.is_snapshot("v1.0")
    assert not release.is_snapshot("20261103.141205")


def _sbom(name: str, fix: bool = False) -> str:
    pkg = {
        "SPDXID": f"SPDXRef-Package-{name}",
        "name": name,
        "versionInfo": "1.0",
        "externalRefs": [
            {"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl",
             "referenceLocator": f"pkg:generic/{name}@1.0"},
        ],
    }  # fmt: skip
    if fix:
        pkg["externalRefs"].append(
            {"referenceCategory": "SECURITY", "referenceType": "fix",
             "referenceLocator": "https://example.com/commit/abc",
             "comment": "CVE-2026-0001 fixed by 0001-fix.patch"}
        )  # fmt: skip
    return json.dumps({"spdxVersion": "SPDX-2.3", "packages": [pkg], "relationships": []})


def artifacts(root: Path, archs=ARCHS, exts=EXTS, ods=True) -> Path:
    """What download-artifact leaves for one CI run, small."""
    for arch in archs:
        r = root / f"rootfs.erofs-{arch}"
        r.mkdir(parents=True)
        (r / "rootfs.erofs").write_bytes(b"erofs " + arch.encode())
        (r / "rootfs.cmdline").write_text("root=/dev/dm-0\n")
        k = root / f"package-kernel-{arch}"
        k.mkdir()
        src = root / f"_kernel-{arch}"
        (src / "rootfs" / "boot").mkdir(parents=True)
        (src / "rootfs" / "boot" / "bzImage").write_bytes(b"kernel " + arch.encode())
        with tarfile.open(k / "kernel.tar", "w") as t:
            t.add(src / "rootfs", arcname="rootfs")
        e = root / f"extensions-{arch}"
        for name, confext in exts.items():
            for kind, d in (("sysext", "extensions"), ("confext", "confexts")):
                if kind == "confext" and not confext:
                    continue
                (e / d).mkdir(parents=True, exist_ok=True)
                (e / d / f"{name}.raw").write_bytes(f"{kind} {name}".encode())
                (e / d / f"{name}.dm-verity").write_text("0 8 verity\n")
        s = root / f"sbom-{arch}"
        s.mkdir()
        (s / "sbom-erofs.json").write_text(_sbom("bash", fix=True))
        (s / "sbom-oci.json").write_text(_sbom("bash"))
        for name in exts:
            (s / f"sbom-ext-{name}.json").write_text(_sbom(name))
        if ods:
            c = root / f"cve-{arch}"
            c.mkdir()
            (c / "cve.ods").write_bytes(b"ods")
    return root


def test_every_asset_gets_its_release_name(tmp_path):
    out = tmp_path / "out"
    a = release.assemble(artifacts(tmp_path / "a"), out, ARCHS, set(EXTS))
    names = sorted(p.name for p in out.iterdir())
    for arch in ARCHS:
        for want in (
            f"rootfs-{arch}.erofs", f"rootfs-{arch}.cmdline", f"kernel-{arch}",
            f"podman-{arch}.sysext.raw", f"podman-{arch}.sysext.dm-verity",
            f"kubernetes-{arch}.confext.raw", f"kubernetes-{arch}.confext.dm-verity",
            f"sbom-{arch}.json", f"sbom-oci-{arch}.json", f"sbom-ext-debug-{arch}.json",
            f"vex-{arch}.json", f"cve-{arch}.ods",
        ):  # fmt: skip
            assert want in names
    assert "podman-amd64.confext.raw" not in names
    assert (out / "kernel-arm64").read_bytes() == b"kernel arm64"
    assert a.missing_optional == []


def test_sha256sums_lists_every_other_asset(tmp_path):
    out = tmp_path / "out"
    release.assemble(artifacts(tmp_path / "a"), out, ARCHS, set(EXTS))
    p = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=out, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    listed = {line.split()[1] for line in (out / "SHA256SUMS").read_text().splitlines()}
    assert listed == {p.name for p in out.iterdir()} - {"SHA256SUMS"}


def test_the_vex_is_made_from_the_disk_sbom(tmp_path):
    out = tmp_path / "out"
    release.assemble(artifacts(tmp_path / "a"), out, ARCHS, set(EXTS))
    vex = json.loads((out / "vex-amd64.json").read_text())
    assert vex["statements"][0]["vulnerability"]["name"] == "CVE-2026-0001"


def test_a_missing_required_asset_fails(tmp_path):
    a = artifacts(tmp_path / "a")
    (a / "rootfs.erofs-arm64" / "rootfs.cmdline").unlink()
    with pytest.raises(release.Missing, match=r"rootfs\.erofs-arm64/rootfs\.cmdline"):
        release.assemble(a, tmp_path / "out", ARCHS, set(EXTS))


def test_an_extension_one_arch_lacks_fails(tmp_path):
    a = artifacts(tmp_path / "a")
    (a / "extensions-arm64" / "extensions" / "debug.raw").unlink()
    with pytest.raises(release.Missing, match=r"debug\.raw"):
        release.assemble(a, tmp_path / "out", ARCHS, set(EXTS))


def test_the_workbook_is_optional(tmp_path):
    a = release.assemble(artifacts(tmp_path / "a", ods=False), tmp_path / "out", ARCHS, set(EXTS))
    assert a.missing_optional == ["cve-amd64.ods", "cve-arm64.ods"]


def test_a_kernel_tar_with_an_unsafe_member_is_refused(tmp_path):
    a = artifacts(tmp_path / "a")
    k = a / "package-kernel-amd64" / "kernel.tar"
    evil = tmp_path / "evil"
    evil.write_text("x")
    with tarfile.open(k, "w") as t:
        t.add(evil, arcname="rootfs/boot/bzImage")
        t.add(evil, arcname="../escape")
    with pytest.raises(release.Missing, match="unsafe"):
        release.assemble(a, tmp_path / "out", ARCHS, set(EXTS))


class FakeGh:
    """gh, answering from a dict of repository state; records what it was asked to do."""

    def __init__(self, head="c" * 40, latest=None, compare="ahead", tags=(), releases=()):
        self.head, self.latest, self.status = head, latest, compare
        self.tags, self.releases, self.calls = set(tags), list(releases), []

    def __call__(self, argv, **_kw):
        self.calls.append(argv)

        def ok(out=""):
            return subprocess.CompletedProcess(argv, 0, out, "")

        def missing():
            return subprocess.CompletedProcess(argv, 1, "", "HTTP 404: Not Found")

        if argv[:2] == ["gh", "api"]:
            path = argv[2]
            if path.endswith("/commits/main"):
                return ok(self.head + "\n")
            if path.endswith("/releases/latest"):
                if self.latest is None:
                    return missing()
                return ok(
                    json.dumps({"tag_name": self.latest[0], "target_commitish": self.latest[1]})
                )
            if "/compare/" in path:
                return ok(self.status + "\n")
            if "/releases/tags/" in path:
                return ok("{}") if path.rsplit("/", 1)[1] in self.tags else missing()
            if path.endswith("/releases?per_page=100"):
                return ok("".join(f"{t}\t{c}\t{str(d).lower()}\n" for t, c, d in self.releases))
        if argv[:3] == ["gh", "release", "download"]:
            return missing()  # no previous SBOMs: the notes say so
        if argv[:3] == ["gh", "release", "create"]:
            self.notes = Path(argv[argv.index("--notes-file") + 1]).read_text()
        if argv[:2] == ["gh", "release"]:
            return ok()
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")

    def did(self, *prefix):
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


def _publish(tmp_path, gh, sha="c" * 40, now=None):
    out = tmp_path / "out"
    if not out.exists():
        release.assemble(artifacts(tmp_path / "a"), out, ARCHS, set(EXTS))
    log = io.StringIO()
    rc = release.publish(
        release.Releases("o/r", gh), out, name="20261104.120000-7", sha=sha,
        run_url="https://github.com/o/r/actions/runs/1", images=["ghcr.io/x/sources:1"],
        now=now or datetime(2026, 11, 4, 12, 0, tzinfo=UTC), out=log,
    )  # fmt: skip
    return rc, log.getvalue()


def test_a_commit_that_is_not_mains_head_is_not_published(tmp_path):
    gh = FakeGh(head="d" * 40)
    rc, log = _publish(tmp_path, gh)
    assert rc == 0
    assert "no longer the head of main" in log
    assert not gh.did("gh", "release", "create")


@pytest.mark.parametrize("status", ["identical", "behind", "diverged"])
def test_only_forward_from_the_latest_snapshot(tmp_path, status):
    gh = FakeGh(latest=("20261101.000000-5", "b" * 40), compare=status)
    rc, log = _publish(tmp_path, gh)
    assert rc == 0
    assert status in log
    assert not gh.did("gh", "release", "create")


def test_an_existing_tag_is_left_alone(tmp_path):
    gh = FakeGh(tags={"20261104.120000-7"})
    rc, log = _publish(tmp_path, gh)
    assert rc == 0
    assert "already exists" in log
    assert not gh.did("gh", "release", "create")


def test_a_snapshot_is_a_draft_until_everything_is_up(tmp_path):
    gh = FakeGh(latest=("20261101.000000-5", "b" * 40))
    rc, _ = _publish(tmp_path, gh)
    assert rc == 0
    (create,) = gh.did("gh", "release", "create")
    assert "--draft" in create and create[create.index("--target") + 1] == "c" * 40
    assets = [a for a in create if a.startswith(str(tmp_path))]
    assert any(a.endswith("SHA256SUMS") for a in assets) and len(assets) > 30
    (edit,) = gh.did("gh", "release", "edit")
    assert "--draft=false" in edit and "--latest" in edit
    assert gh.calls.index(create) < gh.calls.index(edit)


def test_the_notes_say_what_a_snapshot_is(tmp_path):
    gh = FakeGh()
    _publish(tmp_path, gh)
    notes = gh.notes
    assert "Rolling snapshot. No fixes are backported" in notes
    assert "c" * 40 in notes and "actions/runs/1" in notes
    assert "ghcr.io/x/sources:1" in notes
    assert "releases/latest/download/$f" in notes and "rootfs-amd64.erofs" in notes
    assert "first snapshot" in notes


def test_old_snapshots_expire_and_the_latest_never(tmp_path):
    releases = [
        ("20261104.120000-7", "2026-09-01T00:00:00Z", False),  # the one just published
        ("20260901.000000-1", "2026-09-01T00:00:00Z", False),  # old: deleted
        ("20261001.000000-2", "2026-10-01T00:00:00Z", False),  # 34 days: kept
        ("20260801.000000-0", "2026-08-01T00:00:00Z", False),  # the latest until now
        ("20260701.000000-9", "2026-07-01T00:00:00Z", True),  # a draft: not ours to judge
        ("v-something", "2026-01-01T00:00:00Z", False),  # not a snapshot: kept
    ]
    gh = FakeGh(latest=("20260801.000000-0", "b" * 40), releases=releases)
    _publish(tmp_path, gh)
    deleted = sorted(c[3] for c in gh.did("gh", "release", "delete"))
    assert deleted == ["20260801.000000-0", "20260901.000000-1"]
    assert all("--cleanup-tag" in c for c in gh.did("gh", "release", "delete"))


def test_main_refuses_a_name_that_is_not_a_snapshots(tmp_path, capsys):
    assert release.main(["publish", str(tmp_path), "--name", "latest"]) == 2
    assert "not a snapshot name" in capsys.readouterr().err


def test_main_assembles_into_an_empty_directory_only(tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    (out / "stray").write_text("x")
    assert release.main(["assemble", str(tmp_path / "a"), str(out)]) == 2
    assert "not empty" in capsys.readouterr().err


def test_main_says_which_artifact_is_missing(tmp_path, capsys):
    (tmp_path / "a").mkdir()
    assert release.main(["assemble", str(tmp_path / "a"), str(tmp_path / "out")]) == 1
    assert "rootfs.erofs-amd64/rootfs.erofs" in capsys.readouterr().err
