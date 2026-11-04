import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_gh import FakeGh

from kaetzli import image_diff, repo, report
from kaetzli.gh import Gh
from kaetzli.size.sizes import MINUS


def sbom(packages, modules=()):
    """A minimal SPDX document as image/sbom.py writes it: packages {name: version},
    modules [(owner, module, version)]."""
    pkgs, rels = [], []
    for name, version in packages.items():
        pkgs.append({"SPDXID": f"SPDXRef-Package-{name}", "name": name, "versionInfo": version})
    for owner, module, version in modules:
        mid = f"SPDXRef-Module-{owner}-{module.replace('/', '-')}-{version}"
        pkgs.append({"SPDXID": mid, "name": module, "versionInfo": version})
        rels.append({"spdxElementId": f"SPDXRef-Package-{owner}",
                     "relationshipType": "CONTAINS", "relatedSpdxElement": mid})  # fmt: skip
    pkgs.append({"SPDXID": "SPDXRef-Builder-x", "name": "builder", "versionInfo": "1"})
    pkgs.append({"SPDXID": "SPDXRef-Unresolved-libx.so.1", "name": "libx.so.1",
                 "versionInfo": "NOASSERTION"})  # fmt: skip
    return json.dumps({"packages": pkgs, "relationships": rels})


def side(d: Path, *, disk, oci=None, exts=None, files=None, sizes=None):
    d.mkdir(parents=True, exist_ok=True)
    (d / "sbom-erofs.json").write_text(sbom(*disk) if isinstance(disk, tuple) else sbom(disk))
    base = disk[0] if isinstance(disk, tuple) else disk
    (d / "sbom-oci.json").write_text(sbom(oci if oci is not None else base))
    for name, pkgs in (exts or {}).items():
        (d / f"sbom-ext-{name}.json").write_text(sbom(pkgs))
    for flavour, paths in (files or {}).items():
        (d / f"rootfs-files-{flavour}.txt").write_text(
            f"# flavour: {flavour}\n" + "".join(p + "\n" for p in paths)
        )
    for flavour, text in (sizes or {}).items():
        (d / f"rootfs-size-{flavour}.txt").write_text(text)
    return d


BASE_FILES = [
    "etc", "usr", "usr/bin", "usr/bin/bash", "usr/bin/routel",
    "usr/lib", "usr/lib/libssl.so.3", "usr/lib/libssl.so.3.5.3",
    "usr/share", "usr/share/old", "usr/share/old/a", "usr/share/old/b",
]  # fmt: skip
NEW_FILES = [
    "etc", "usr", "usr/bin", "usr/bin/bash", "usr/bin/jq",
    "usr/lib", "usr/lib/libssl.so.3", "usr/lib/libssl.so.3.5.4",
    "usr/share", "usr/share/new", "usr/share/new/x", "usr/share/new/y", "usr/share/new/z",
]  # fmt: skip

SIZES_OLD = "total 104857600\ndisk 52428800\ndir 83886080 usr\ndir 20971520 usr/lib\n"
SIZES_NEW = "total 106954752\ndisk 53477376\ndir 85983232 usr\ndir 23068672 usr/lib\n"


@pytest.fixture
def pair(tmp_path):
    return make_pair(tmp_path)


def make_pair(tmp_path):
    old = side(
        tmp_path / "old",
        disk=({"bash": "5.3", "openssl": "3.5.3", "iproute2": "6.17", "kernel": "6.17.1",
               "podman-vendor": "5.6"},
              [("podman", "golang.org/x/sys", "0.30.0"), ("podman", "github.com/gone", "1")]),
        oci={"bash": "5.3", "openssl": "3.5.3", "iproute2": "6.17"},
        exts={"podman": {"podman": "5.6.0"}, "debug": {"strace": "6.17"}},
        files={"erofs": BASE_FILES, "oci": BASE_FILES},
        sizes={"erofs": SIZES_OLD, "oci": "total 52428800\n"},
    )  # fmt: skip
    new = side(
        tmp_path / "new",
        disk=({"bash": "5.3", "openssl": "3.5.4", "iproute2": "6.17", "kernel": "6.17.1",
               "jq": "1.8", "podman-vendor": "5.6"},
              [("podman", "golang.org/x/sys", "0.31.0"), ("podman", "github.com/new", "2")]),
        oci={"bash": "5.3", "openssl": "3.5.4", "jq": "1.8"},
        exts={"podman": {"podman": "5.7.0"}, "k8s": {"kubelet": "1.34"}},
        files={"erofs": NEW_FILES, "oci": BASE_FILES},
        sizes={"erofs": SIZES_NEW, "oci": "total 52428800\n"},
    )  # fmt: skip
    return old, new


def render(old, new, **kw):
    return "\n".join(image_diff.render(image_diff.load(old), image_diff.load(new), **kw)) + "\n"


class TestPackages:
    def test_changed_added_removed_and_where(self, pair):
        rows = image_diff.package_rows(*map(image_diff.load, pair))
        assert rows == [
            ("debug: strace", "6.17", "—", f"{MINUS}debug"),
            ("iproute2", "6.17", "6.17", f"disk, {MINUS}oci"),
            ("jq", "—", "1.8", "+disk, +oci"),
            ("k8s: kubelet", "—", "1.34", "+k8s"),
            ("openssl", "3.5.3", "3.5.4", "disk, oci"),
            ("podman: podman", "5.6.0", "5.7.0", "podman"),
        ]

    def test_builders_unresolved_libraries_and_modules_are_not_packages(self, pair):
        names = image_diff.load(pair[0]).packages["disk"]
        assert set(names) == {"bash", "openssl", "iproute2", "kernel", "podman-vendor"}

    def test_modules_by_owner(self, pair):
        rows = image_diff.module_rows(*map(image_diff.load, pair))
        assert rows == [
            ("podman", "github.com/gone", "1", "—"),
            ("podman", "github.com/new", "—", "2"),
            ("podman", "golang.org/x/sys", "0.30.0", "0.31.0"),
        ]

    def test_a_new_package_brings_no_module_rows(self, tmp_path):
        a = side(tmp_path / "a", disk={"crun": "1"})
        b = side(tmp_path / "b", disk=({"crun": "1", "netavark": "2"},
                                       [("netavark", "serde", "1.0")]))  # fmt: skip
        assert image_diff.module_rows(image_diff.load(a), image_diff.load(b)) == []


class TestPaths:
    def test_collapse_keeps_the_top_of_a_new_tree(self):
        shown = image_diff.collapse({"a", "a/b", "a/b/c", "d/e"})
        assert shown == [("a", 2), ("d/e", 0)]

    def test_version_renames_are_paired(self):
        changes = image_diff.path_changes(set(BASE_FILES), set(NEW_FILES))
        assert changes.renamed == [("usr/lib/libssl.so.3.5.3", "usr/lib/libssl.so.3.5.4")]
        assert changes.added == [("usr/bin/jq", 0), ("usr/share/new", 3)]
        assert changes.removed == [("usr/bin/routel", 0), ("usr/share/old", 2)]

    def test_only_unique_patterns_pair(self):
        changes = image_diff.path_changes({"lib/a1", "lib/a2"}, {"lib/a3"})
        assert changes.renamed == []
        assert changes.added == [("lib/a3", 0)]

    def test_diff_lines(self):
        changes = image_diff.path_changes(set(BASE_FILES), set(NEW_FILES))
        assert image_diff.diff_lines(changes) == [
            "+ usr/bin/jq",
            "- usr/bin/routel",
            "~ usr/lib/libssl.so.3.5.3 → usr/lib/libssl.so.3.5.4",
            "+ usr/share/new/  (3 paths below)",
            "- usr/share/old/  (2 paths below)",
        ]

    def test_long_lists_are_cut(self):
        changes = image_diff.path_changes(set(), {f"f{i:03}" for i in range(10)})
        lines = image_diff.diff_lines(changes, limit=4)
        assert lines == ["+ f000", "+ f001", "+ f002", "+ f003", "… and 6 more"]


class TestReport:
    def test_the_whole_report(self, pair):
        out = render(*pair, arch="amd64", against="run 41 (`aaaaaaa`) on `main`")
        assert out == (Path(__file__).parent / "fixtures" / "image-diff" / "report.md").read_text()

    def test_no_change(self, tmp_path):
        files = {"erofs": BASE_FILES, "oci": BASE_FILES}
        sizes = {"erofs": SIZES_OLD, "oci": "total 1\n"}
        a = side(tmp_path / "a", disk={"bash": "5.3"}, files=files, sizes=sizes)
        b = side(tmp_path / "b", disk={"bash": "5.3"}, files=files, sizes=sizes)
        diff = image_diff.Diff(image_diff.load(a), image_diff.load(b))
        assert not diff.changed()
        out = render(a, b, arch="amd64", against="x")
        assert "No package, path or size changed." in out

    def test_a_side_without_manifests_says_so(self, pair):
        for p in pair[0].glob("rootfs-files-*.txt"):
            p.unlink()
        out = render(*pair, arch="amd64", against="x")
        assert "_No path manifests on one side; paths not compared._" in out

    def test_small_size_moves_are_not_a_change(self, tmp_path):
        a = side(tmp_path / "a", disk={"bash": "5.3"}, sizes={"erofs": "total 1000000\n"})
        b = side(tmp_path / "b", disk={"bash": "5.3"}, sizes={"erofs": "total 1000100\n"})
        assert not image_diff.Diff(image_diff.load(a), image_diff.load(b)).changed()


def fake_gh(tmp_path, pair, extra=()):
    old = pair[0]
    downloads = {
        ("41", "rootfs-size-amd64"): {p.name: p.read_text() for p in old.glob("rootfs-*.txt")},
        ("41", "sbom-amd64"): {p.name: p.read_text() for p in old.glob("sbom-*.json")},
    }
    runs = [
        {"databaseId": 50, "headSha": "c" * 40, "createdAt": "2026-11-02T10:00:00Z"},
        {"databaseId": 45, "headSha": "b" * 40, "createdAt": "2026-11-01T10:00:00Z"},
        {"databaseId": 41, "headSha": "a" * 40, "createdAt": "2026-10-31T10:00:00Z"},
    ]
    answers = [
        (["gh", "auth", "status"], (0, "")),
        (["gh", "run", "list"], (0, json.dumps(runs))),
        *extra,
    ]
    return FakeGh(answers, downloads)


def compare(base, head, status="ahead", files=("docs/x.md",)):
    """gh api compare BASE...HEAD, as Gh.compare asks it."""
    out = json.dumps({"status": status, "files": [{"filename": f} for f in files]})
    return (["gh", "api", f"repos/{{owner}}/{{repo}}/compare/{base}...{head}"], (0, out))


class TestAgainstMain:
    def test_the_build_of_the_base_itself(self, pair, tmp_path):
        # Run 50 is this one, 45 is main moving on past the base: neither is compared to.
        fake = fake_gh(tmp_path, pair)
        out = io.StringIO()
        diff = image_diff.against_main(
            Gh(run=fake), "amd64", pair[1], report.Report(stdout=out), skip_run="50",
            base="a" * 40,
        )  # fmt: skip
        assert diff.changed()
        assert "against run 41 (`aaaaaaa`), the build of this branch's base on `main`" in (
            out.getvalue()
        )
        downloads = [c[3] for c in fake.calls if c[:3] == ["gh", "run", "download"]]
        assert "50" not in downloads and "45" not in downloads
        assert downloads[-2:] == ["41", "41"]

    def test_a_build_before_the_base_with_only_documentation_since(self, pair, tmp_path):
        base = "d" * 40
        fake = fake_gh(tmp_path, pair, extra=[
            compare("c" * 40, base, status="behind"),  # main moved past the base
            compare("b" * 40, base, files=("src/x.c",)),  # code since: its build is not the base's
            compare("a" * 40, base, files=("README.md", "docs/guides/x.md")),
        ])  # fmt: skip
        out = io.StringIO()
        image_diff.against_main(
            Gh(run=fake), "amd64", pair[1], report.Report(stdout=out), base=base
        )
        assert "against run 41 (`aaaaaaa`)" in out.getvalue()
        assert "documentation only since" in out.getvalue()

    def test_no_build_of_the_base_is_a_note_not_a_wrong_comparison(self, pair, tmp_path):
        # Main's newest green build is newer than the base: comparing to it would credit
        # this branch with what main merged since. This is how cri-o 1.37.3 appeared on a
        # pull request that did not touch it.
        base = "e" * 40
        fake = fake_gh(tmp_path, pair, extra=[
            compare("c" * 40, base, status="behind"),
            compare("b" * 40, base, status="behind"),
            compare("a" * 40, base, status="behind"),
        ])  # fmt: skip
        with pytest.raises(report.Note, match="no green build of this branch's base `eeeeeee`"):
            image_diff.against_main(
                Gh(run=fake), "amd64", pair[1], report.Report(stdout=io.StringIO()), base=base
            )
        assert not [c for c in fake.calls if c[:3] == ["gh", "run", "download"]]

    def test_without_gh_it_is_a_note(self, pair):
        with pytest.raises(report.Note, match="no `gh`"):
            image_diff.against_main(
                Gh(which=lambda _: None),
                "amd64",
                pair[1],
                report.Report(stdout=io.StringIO()),
                base="a" * 40,
            )

    def test_no_baseline_is_a_note(self, pair):
        fake = FakeGh([(["gh", "auth", "status"], (0, "")), (["gh", "run", "list"], (0, "[]"))])
        with pytest.raises(report.Note, match="no green build of this branch's base"):
            image_diff.against_main(
                Gh(run=fake),
                "amd64",
                pair[1],
                report.Report(stdout=io.StringIO()),
                base="a" * 40,
            )


class TestComment:
    MARK = "<!-- image-diff:amd64 -->"

    def test_an_existing_comment_is_edited(self):
        fake = FakeGh(
            [
                (["gh", "pr", "list"], (0, "7\n")),
                (["gh", "api", "--paginate", "repos/{owner}/{repo}/issues/7/comments"],
                 (0, "1234\n")),
                (["gh", "api", "-X", "PATCH"], (0, "")),
            ]
        )  # fmt: skip
        assert image_diff.comment(Gh(run=fake), "branch", self.MARK, "body", changed=False)
        patch = next(c for c in fake.calls if "PATCH" in c)
        assert "repos/{owner}/{repo}/issues/comments/1234" in patch
        assert f"body={self.MARK}\nbody" in patch

    def test_a_new_comment_only_for_a_change(self):
        answers = [
            (["gh", "pr", "list"], (0, "7\n")),
            (["gh", "api", "--paginate"], (0, "")),
            (["gh", "api", "-X", "POST"], (0, "")),
        ]
        fake = FakeGh(answers)
        assert not image_diff.comment(Gh(run=fake), "b", self.MARK, "body", changed=False)
        assert not any("POST" in c for c in fake.calls)
        fake = FakeGh(answers)
        assert image_diff.comment(Gh(run=fake), "b", self.MARK, "body", changed=True)
        post = next(c for c in fake.calls if "POST" in c)
        assert "repos/{owner}/{repo}/issues/7/comments" in post

    def test_no_pull_request_no_comment(self):
        fake = FakeGh([(["gh", "pr", "list"], (0, ""))])
        assert not image_diff.comment(Gh(run=fake), "main", self.MARK, "body", changed=True)


def test_the_entry_point(pair, tmp_path):
    env = dict(os.environ)
    env.pop("GITHUB_STEP_SUMMARY", None)
    p = subprocess.run(
        [sys.executable, repo.ROOT / "test" / "image-diff.py", "--arch", "amd64", *map(str, pair)],
        env=env, capture_output=True, text=True, cwd=tmp_path, check=False,
    )  # fmt: skip
    assert p.returncode == 0, p.stderr
    assert "| `openssl` | 3.5.3 | 3.5.4 | disk, oci |" in p.stdout


class TestBaseCommit:
    def test_a_branch_is_compared_with_where_it_forked(self):
        fake = FakeGh([(["gh", "api", "repos/{owner}/{repo}/compare/main...f00"],
                        (0, json.dumps({"merge_base_commit": {"sha": "a" * 40}})))])  # fmt: skip
        assert image_diff.base_commit(Gh(run=fake), "topic", "main", "f00") == "a" * 40

    def test_main_is_compared_with_the_commit_before(self):
        fake = FakeGh([(["gh", "api", "repos/{owner}/{repo}/commits/f00"],
                        (0, json.dumps({"parents": [{"sha": "b" * 40}]})))])  # fmt: skip
        assert image_diff.base_commit(Gh(run=fake), "main", "main", "f00") == "b" * 40

    def test_no_answer_is_none(self):
        assert image_diff.base_commit(Gh(run=FakeGh([])), "topic", "main", "f00") is None
