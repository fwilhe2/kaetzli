import hashlib
import io
import json
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kaetzli import release
from kaetzli.release import Compare

A, B, C, D = "a" * 40, "b" * 40, "c" * 40, "d" * 40


# --- the name ---------------------------------------------------------------------------


def test_name_is_the_committer_date_in_utc_and_the_run_number():
    epoch = int(datetime(2026, 11, 3, 14, 12, 5, tzinfo=UTC).timestamp())
    assert release.snapshot_name(epoch, 42) == "20261103.141205-42"
    assert release.NAME.match("20261103.141205-42")
    assert not release.NAME.match("v1.0")


# --- whether to publish -----------------------------------------------------------------


def compares(table):
    def compare(base, head):
        return table[(base, head)]

    return compare


def test_first_snapshot_publishes():
    d = release.decide(A, A, None, compares({}))
    assert d.action == "publish"


def test_the_latest_snapshots_own_commit_is_not_published_again():
    # the scheduled fresh rebuild of an unchanged main, or a re-run after publishing
    d = release.decide(B, B, B, compares({}))
    assert d.action == "skip" and "already" in d.reason


def test_a_commit_main_has_moved_past_is_skipped():
    d = release.decide(A, B, None, compares({(A, B): Compare("ahead", ["packages/x/env.sh"])}))
    assert d.action == "skip" and B[:12] in d.reason


def test_a_commit_followed_only_by_documentation_is_still_current():
    # A docs-only push skips the build, so nothing after A would ever publish A's image.
    d = release.decide(
        A,
        B,
        None,
        compares({(A, B): Compare("ahead", ["docs/proposals/release.md", "README.md"])}),
    )
    assert d.action == "publish"


def test_too_many_files_to_list_is_not_documentation():
    d = release.decide(A, B, None, compares({(A, B): Compare("ahead", None)}))
    assert d.action == "skip"


def test_main_rewritten_under_the_run_is_skipped():
    d = release.decide(A, B, None, compares({(A, B): Compare("diverged", ["x"])}))
    assert d.action == "skip"


def test_newer_than_the_latest_snapshot_publishes():
    d = release.decide(C, C, A, compares({(A, C): Compare("ahead", ["packages/x/env.sh"])}))
    assert d.action == "publish"


def test_only_documentation_since_the_latest_snapshot_is_not_a_new_one():
    d = release.decide(C, C, A, compares({(A, C): Compare("ahead", ["docs/x.md"])}))
    assert d.action == "skip" and "nothing the build reads" in d.reason


def test_older_than_the_latest_snapshot_is_skipped():
    # A built and published D (a fresh rebuild of a docs-only head); A is still "current"
    # by the docs rule, and must not go backwards.
    d = release.decide(
        A,
        D,
        D,
        compares(
            {
                (A, D): Compare("ahead", ["docs/x.md"]),
                (D, A): Compare("behind", []),
            }
        ),
    )
    assert d.action == "skip" and "older" in d.reason


def test_a_latest_snapshot_off_main_needs_a_human():
    # main was rewritten: no later commit can ever descend from the latest snapshot
    d = release.decide(C, C, A, compares({(A, C): Compare("diverged", ["x"])}))
    assert d.action == "fail"


# --- the assets -------------------------------------------------------------------------


def kernel_tar(path: Path, member: str = "rootfs/boot/bzImage", data: bytes = b"kernel"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w") as t:
        info = tarfile.TarInfo(member)
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
        cfg = tarfile.TarInfo("rootfs/boot/config")
        cfg.size = 2
        t.addfile(cfg, io.BytesIO(b"y\n"))


def sbom(packages=()):
    return json.dumps({"spdxVersion": "SPDX-2.3", "packages": list(packages)})


FIXED = {
    "name": "bash",
    "versionInfo": "5.3",
    "externalRefs": [
        {"referenceType": "purl", "referenceLocator": "pkg:generic/bash@5.3"},
        {"referenceType": "fix", "comment": "CVE-2026-0001 fixed by 0001-x.patch"},
    ],
}


def artifacts(root: Path, arch: str, exts=("podman", "debug"), confexts=("podman",)):
    def w(rel, data="x"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)

    w(f"rootfs.erofs-{arch}/rootfs.erofs", "erofs")
    w(f"rootfs.erofs-{arch}/rootfs.cmdline", "root=/dev/dm-0")
    kernel_tar(root / f"package-kernel-{arch}/kernel.tar")
    for e in exts:
        w(f"extensions-{arch}/extensions/{e}.raw")
        w(f"extensions-{arch}/extensions/{e}.dm-verity")
        w(f"sbom-{arch}/sbom-ext-{e}.json", sbom())
    for e in confexts:
        w(f"extensions-{arch}/confexts/{e}.raw")
        w(f"extensions-{arch}/confexts/{e}.dm-verity")
    w(f"sbom-{arch}/sbom-erofs.json", sbom([FIXED]))
    w(f"sbom-{arch}/sbom-oci.json", sbom([FIXED]))
    w(f"cve-{arch}/cve.ods", "ods")
    w(f"cve-{arch}/cve-summary.txt", "db 2026-11-03T00:00:00Z grype-0.1\n")


def test_collect_names_every_asset_by_arch(tmp_path):
    artifacts(tmp_path / "a", "amd64")
    got = release.collect(tmp_path / "a", tmp_path / "out", "amd64", ["debug", "podman"])
    assert got.problems == []
    names = sorted(a.name for a in got.assets)
    assert names == [
        "cve-amd64.ods",
        "debug-amd64.sysext.dm-verity",
        "debug-amd64.sysext.raw",
        "kernel-amd64",
        "podman-amd64.confext.dm-verity",
        "podman-amd64.confext.raw",
        "podman-amd64.sysext.dm-verity",
        "podman-amd64.sysext.raw",
        "rootfs-amd64.cmdline",
        "rootfs-amd64.erofs",
        "sbom-amd64.json",
        "sbom-ext-debug-amd64.json",
        "sbom-ext-podman-amd64.json",
        "sbom-oci-amd64.json",
        "vex-amd64.json",
    ]
    assert (tmp_path / "out/kernel-amd64").read_bytes() == b"kernel"
    vex = json.loads((tmp_path / "out/vex-amd64.json").read_text())
    # one statement, though both the disk and the OCI SBOM carry the fix
    assert [s["vulnerability"]["name"] for s in vex["statements"]] == ["CVE-2026-0001"]
    assert got.cve_db == "2026-11-03T00:00:00Z"


def test_a_missing_required_asset_is_a_problem_and_names_it(tmp_path):
    artifacts(tmp_path / "a", "arm64")
    (tmp_path / "a/rootfs.erofs-arm64/rootfs.cmdline").unlink()
    (tmp_path / "a/extensions-arm64/extensions/debug.raw").unlink()
    got = release.collect(tmp_path / "a", tmp_path / "out", "arm64", ["debug", "podman"])
    assert any("rootfs.cmdline" in p for p in got.problems)
    assert any("debug" in p and "sysext" in p for p in got.problems)


def test_an_extension_named_in_env_sh_but_not_built_is_a_problem(tmp_path):
    artifacts(tmp_path / "a", "amd64", exts=("podman",))
    got = release.collect(tmp_path / "a", tmp_path / "out", "amd64", ["debug", "podman"])
    assert any("debug" in p for p in got.problems)


def test_half_a_confext_is_a_problem(tmp_path):
    artifacts(tmp_path / "a", "amd64")
    (tmp_path / "a/extensions-amd64/confexts/podman.dm-verity").unlink()
    got = release.collect(tmp_path / "a", tmp_path / "out", "amd64", ["debug", "podman"])
    assert any("podman" in p and "confext" in p for p in got.problems)


def test_the_workbook_is_optional(tmp_path):
    artifacts(tmp_path / "a", "amd64")
    (tmp_path / "a/cve-amd64/cve.ods").unlink()
    got = release.collect(tmp_path / "a", tmp_path / "out", "amd64", ["debug", "podman"])
    assert got.problems == []
    assert got.workbook is False


def test_no_fix_reference_means_no_vex(tmp_path):
    artifacts(tmp_path / "a", "amd64")
    for f in ("sbom-erofs.json", "sbom-oci.json"):
        (tmp_path / "a/sbom-amd64" / f).write_text(sbom())
    got = release.collect(tmp_path / "a", tmp_path / "out", "amd64", ["debug", "podman"])
    assert got.problems == []
    assert "vex-amd64.json" not in [a.name for a in got.assets]


def test_a_kernel_tar_without_the_image_is_a_problem(tmp_path):
    artifacts(tmp_path / "a", "amd64")
    kernel_tar(tmp_path / "a/package-kernel-amd64/kernel.tar", member="rootfs/boot/other")
    got = release.collect(tmp_path / "a", tmp_path / "out", "amd64", ["debug", "podman"])
    assert any("bzImage" in p for p in got.problems)


def test_sha256sums_is_sha256sum_c_format_sorted(tmp_path):
    (tmp_path / "b").write_text("2")
    (tmp_path / "a").write_text("1")
    assets = [release.Asset("b", tmp_path / "b"), release.Asset("a", tmp_path / "a")]
    text = release.sha256sums(assets)
    h = hashlib.sha256(b"1").hexdigest()
    assert text.splitlines()[0] == f"{h}  a"
    assert text.endswith("  b\n")


# --- expiry -----------------------------------------------------------------------------


def rel(tag, published, draft=False, id_=1):
    return {"id": id_, "tag_name": tag, "draft": draft, "published_at": published, "assets": []}


def test_expired_keeps_the_latest_young_drafts_and_foreign_tags():
    now = datetime(2026, 12, 31, tzinfo=UTC)
    rs = [
        rel("20261101.000000-1", "2026-11-01T00:00:00Z"),  # 60 days: goes
        rel("20261124.000000-2", "2026-11-24T00:00:00Z"),  # exactly 37 days: goes
        rel("20261125.000000-3", "2026-11-25T00:00:00Z"),  # 36 days: stays
        rel("20261001.000000-4", "2026-10-01T00:00:00Z"),  # old, but the latest
        rel("v1.0", "2025-01-01T00:00:00Z"),  # not ours
        rel("20260101.000000-5", None, draft=True),  # a draft
    ]
    gone = release.expired(rs, now, latest_tag="20261001.000000-4")
    assert [r["tag_name"] for r in gone] == ["20261101.000000-1", "20261124.000000-2"]


# --- image digests ----------------------------------------------------------------------


def test_digest_asks_the_registry_for_the_manifest():
    calls = []

    def http(method, url, headers):
        calls.append((method, url))
        if "/token" in url:
            return 200, {}, b'{"token": "t"}'
        assert headers["Authorization"] == "Bearer t"
        return 200, {"docker-content-digest": "sha256:" + "f" * 64}, b""

    got = release.image_digest("ghcr.io/fwilhe2/kaetzli/builder:0123-amd64", http)
    assert got == "sha256:" + "f" * 64
    assert calls[-1] == ("HEAD", "https://ghcr.io/v2/fwilhe2/kaetzli/builder/manifests/0123-amd64")


def test_digest_is_none_when_the_registry_does_not_answer():
    assert release.image_digest("ghcr.io/x/y:t", lambda *a: (404, {}, b"")) is None


# --- the notes --------------------------------------------------------------------------


def test_the_notes_show_how_to_install_the_launcher():
    text = release.notes("20261103.141205-42", B, "o/r", "url", [], None, [], [], {})
    assert "releases/latest/download/kaetzli-$(uname -s" in text


def test_notes_carry_the_notice_provenance_and_boot_instructions():
    text = release.notes(
        name="20261103.141205-42",
        sha=A,
        repo="fwilhe2/kaetzli",
        run_url="https://github.com/fwilhe2/kaetzli/actions/runs/7",
        images=[("sources", "ghcr.io/x/sources:1", "sha256:" + "e" * 64)],
        previous="20261101.000000-41",
        changes=["### What changed"],
        missing_workbooks=["arm64"],
        cve_db={"amd64": "2026-11-03"},
    )
    assert text.startswith("> Rolling snapshot.")
    assert A in text and "actions/runs/7" in text
    assert "sources@sha256:" + "e" * 64 in text.replace("ghcr.io/x/", "")
    assert "releases/latest/download/rootfs-amd64.erofs" in text
    assert "cve-arm64.ods" in text  # the missing workbook is said
    assert "20261101.000000-41" in text


# --- the whole run, dry -----------------------------------------------------------------


class FakeGh:
    """gh, answering from the state of a repository's releases. Records every call."""

    def __init__(self, head=B, releases=(), latest=None, compare=None):
        self.head = head
        self.releases = list(releases)
        self.latest = latest  # (tag, sha) or None
        self.compare = compare or {}
        self.calls = []

    def __call__(self, argv, **_kw):
        self.calls.append(argv)

        def ok(out=""):
            return subprocess.CompletedProcess(argv, 0, out, "")

        def fail(err="HTTP 404"):
            return subprocess.CompletedProcess(argv, 1, "", err)

        if argv[:3] == ["gh", "release", "create"]:
            self.releases.append(
                {"id": 99, "tag_name": argv[3], "draft": True, "published_at": None}
            )
            return ok()
        if "DELETE" in argv and "/releases/" in argv[-1]:
            gone = int(argv[-1].rsplit("/", 1)[1])
            self.releases = [r for r in self.releases if r["id"] != gone]
            return ok()
        if argv[:2] != ["gh", "api"] or "-X" in argv:
            return ok()  # any other write; the dry-run tests assert there are none
        path = next(a for a in argv[2:] if a.startswith("repos/"))
        if path.endswith("/git/ref/heads/main"):
            return ok(json.dumps({"object": {"sha": self.head}}))
        if path.endswith("/releases/latest"):
            return ok(json.dumps({"tag_name": self.latest[0]})) if self.latest else fail()
        if "/commits/" in path:
            return ok(json.dumps({"sha": self.latest[1]}))
        if "/compare/" in path:
            base, head = path.split("/compare/")[1].split("...")
            st, files = self.compare[(base, head)]
            return ok(json.dumps({"status": st, "files": [{"filename": f} for f in files]}))
        if "/releases" in path:
            return ok("".join(json.dumps(r) + "\n" for r in self.releases))
        return fail("unexpected")


WRITES = ("create", "delete", "upload", "edit")


def writes(calls):
    return [c for c in calls if ("-X" in c) or (c[:2] == ["gh", "release"] and c[2] in WRITES)]


def launchers(root: Path) -> Path:
    """What tools/build-launcher.sh leaves, where the release job puts it."""
    d = root / "launcher"
    d.mkdir(parents=True, exist_ok=True)
    for name in release.LAUNCHERS:
        (d / name).write_bytes(b"\x7fELF " + name.encode())
    return d


def test_collect_launcher_takes_every_binary_executable(tmp_path):
    launchers(tmp_path / "artifacts")
    got = release.collect_launcher(tmp_path / "artifacts", tmp_path / "out")
    assert got.problems == []
    assert sorted(a.name for a in got.assets) == sorted(release.LAUNCHERS)
    assert all((tmp_path / "out" / n).stat().st_mode & 0o111 for n in release.LAUNCHERS)


def test_a_missing_launcher_binary_is_a_problem(tmp_path):
    (launchers(tmp_path / "artifacts") / "kaetzli-linux-aarch64").unlink()
    got = release.collect_launcher(tmp_path / "artifacts", tmp_path / "out")
    assert any("kaetzli-linux-aarch64" in p for p in got.problems)


@pytest.fixture
def both_arches(tmp_path):
    for arch in release.ARCHES:
        artifacts(tmp_path / "artifacts", arch)
    launchers(tmp_path / "artifacts")
    return tmp_path


def run_publish(tmp_path, gh, dry_run=True, sha=B):
    return release.publish(
        release.Options(
            repo="o/r",
            sha=sha,
            name="20261103.141205-42",
            run_url="https://example/run",
            artifacts=tmp_path / "artifacts",
            out=tmp_path / "out",
            extensions=["debug", "podman"],
            dry_run=dry_run,
            now=datetime(2026, 11, 3, tzinfo=UTC),
        ),
        release.GitHub("o/r", run=gh),
        images=lambda: [("sources", "ghcr.io/x/sources:1", "sha256:" + "e" * 64)],
    )


def test_dry_run_writes_nothing_and_leaves_the_release_on_disk(both_arches):
    gh = FakeGh(head=B, latest=("20261101.000000-41", A), compare={(A, B): ("ahead", ["x"])})
    assert run_publish(both_arches, gh) == 0
    assert writes(gh.calls) == []
    out = both_arches / "out"
    assert (out / "SHA256SUMS").is_file() and (out / "notes.md").is_file()
    listed = [line.split()[1] for line in (out / "SHA256SUMS").read_text().splitlines()]
    assert "rootfs-amd64.erofs" in listed and "rootfs-arm64.erofs" in listed


def test_dry_run_of_a_skipped_commit_still_collects_and_succeeds(both_arches):
    gh = FakeGh(head=C, compare={(B, C): ("ahead", ["packages/x/env.sh"])})
    assert run_publish(both_arches, gh) == 0
    assert (both_arches / "out/SHA256SUMS").is_file()


def test_dry_run_fails_on_what_the_real_run_would_fail_on(both_arches):
    (both_arches / "artifacts/rootfs.erofs-arm64/rootfs.erofs").unlink()
    gh = FakeGh(head=B)
    assert run_publish(both_arches, gh) == 1
    assert writes(gh.calls) == []


def test_publish_creates_a_draft_then_publishes_it_as_latest(both_arches):
    gh = FakeGh(head=B, latest=("20261101.000000-41", A), compare={(A, B): ("ahead", ["x"])})
    assert run_publish(both_arches, gh, dry_run=False) == 0
    create = next(c for c in gh.calls if c[:3] == ["gh", "release", "create"])
    assert "--draft" in create and "--target" in create and B in create
    assert any(c.endswith("SHA256SUMS") for c in create)
    patch = next(c for c in gh.calls if "PATCH" in c)
    assert "repos/o/r/releases/99" in patch
    assert "draft=false" in patch and "make_latest=true" in patch
    # the draft is made before it is published
    assert gh.calls.index(create) < gh.calls.index(patch)


def test_a_published_release_of_this_name_is_left_alone(both_arches):
    existing = rel("20261103.141205-42", "2026-11-03T00:00:00Z", id_=5)
    gh = FakeGh(head=B, releases=[existing], latest=("20261103.141205-42", B))
    assert run_publish(both_arches, gh, dry_run=False) == 0
    assert not any(c[:3] == ["gh", "release", "create"] for c in gh.calls)


def test_a_draft_left_by_a_failed_attempt_is_replaced(both_arches):
    stale = rel("20261103.141205-42", None, draft=True, id_=5)
    gh = FakeGh(head=B, releases=[stale])
    assert run_publish(both_arches, gh, dry_run=False) == 0
    delete = next(c for c in gh.calls if "DELETE" in c)
    assert "repos/o/r/releases/5" in delete
    create = next(c for c in gh.calls if c[:3] == ["gh", "release", "create"])
    assert gh.calls.index(delete) < gh.calls.index(create)
    # and it is the new draft that is published, not the deleted one
    patch = next(c for c in gh.calls if "PATCH" in c)
    assert "repos/o/r/releases/99" in patch


def test_a_skipped_run_publishes_nothing(both_arches):
    gh = FakeGh(head=C, compare={(B, C): ("ahead", ["packages/x/env.sh"])})
    assert run_publish(both_arches, gh, dry_run=False) == 0
    assert not any(c[:3] == ["gh", "release", "create"] for c in gh.calls)


def test_main_rewritten_fails_the_real_run(both_arches):
    gh = FakeGh(head=B, latest=("20261101.000000-41", A), compare={(A, B): ("diverged", ["x"])})
    assert run_publish(both_arches, gh, dry_run=False) == 1
    assert not any(c[:3] == ["gh", "release", "create"] for c in gh.calls)
