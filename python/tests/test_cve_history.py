import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kaetzli import report
from kaetzli.cve import history, not_affected
from kaetzli.cve.grype import Grype
from kaetzli.cve.summary import Counts, Summary
from kaetzli.gh import Gh

FIXTURES = Path(__file__).parent / "fixtures"


class TestLabel:
    @pytest.mark.parametrize(
        ("subjects", "label"),
        [
            (None, "?"),
            ([], "—"),
            (["Update zlib to 1.3.2", "Update tar to 1.36"], "update"),
            (["docs: x", "kernel: y"], "other"),
            (["Update zlib to 1.3.2", "docs: x"], "mixed"),
            (["Updates zlib to 1.3.2"], "other"),
            (["Update the docs"], "other"),
        ],
    )
    def test_by_who_wrote_the_commits(self, subjects, label):
        assert history.label(subjects) == label

    def test_git_subjects_of_this_repository(self):
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        assert history.git_subjects(head, head) == []
        assert history.git_subjects("0" * 40, head) is None


def summary(total, fixable=0, exts=3):
    docs = [("erofs", Counts(total=total))] + [(f"ext-{i}", Counts()) for i in range(exts)]
    return Summary(db="2026-11-01", grype="grype-0.120.1", docs=docs,
                   all=Counts(total=total, fixable=fixable))  # fmt: skip


def build(run, sha, total, then=None, exts=3, date="2026-10-01"):
    return history.Build(
        run=run, sha=sha, date=date,
        now=summary(total, fixable=total // 2, exts=exts) if total is not None else Summary(),
        then=summary(then) if then is not None else Summary(),
    )  # fmt: skip


class TestTable:
    def test_rows_steps_and_sums(self):
        builds = [
            build("1", "a" * 40, 180, then=180),
            build("2", "b" * 40, 178, then=179),
            build("3", "c" * 40, 178),
            build("4", "d" * 40, 170, then=170),
        ]
        labels = {("a" * 40, "b" * 40): "update", ("b" * 40, "c" * 40): "other",
                  ("c" * 40, "d" * 40): "update"}  # fmt: skip
        lines = history.render(builds, "amd64", lambda a, b: labels[(a, b)])
        assert lines == [
            "## Known vulnerabilities over time — amd64",
            "",
            "Same database: every build rescanned with the vulnerability database of "
            "2026-11-01. At build time: what that build's own scan found.",
            "",
            "| date | commit | change | same database | step | with a fix | at build time "
            "| SBOMs |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
            "| 2026-10-01 | `aaaaaaa` | — | 180 |  | 90 | 180 | disk + 3 ext |",
            "| 2026-10-01 | `bbbbbbb` | update | 178 | -2 | 89 | 179 | disk + 3 ext |",
            "| 2026-10-01 | `ccccccc` | other | 178 | 0 | 89 | — | disk + 3 ext |",
            "| 2026-10-01 | `ddddddd` | update | 170 | -8 | 85 | 170 | disk + 3 ext |",
            "",
            "| series | trend |",
            "| --- | --- |",
            "| same database | █▆▆▁ |",
            "| at build time | █▇▁ |",
            "",
            "Steps summed by what landed between builds (same database, so only the image's "
            "own changes): updates -10, other commits 0, mixed 0.",
        ]

    def test_a_step_where_the_extensions_appear_is_the_sbom_growing(self):
        builds = [build("1", "a" * 40, 50, exts=0), build("2", "b" * 40, 60)]
        lines = history.render(builds, "amd64", lambda a, b: "update")
        assert "| 2026-10-01 | `aaaaaaa` | — | 50 |  | 25 | — | disk |" in lines
        assert (
            "| 2026-10-01 | `bbbbbbb` | update | 60 | (+10, new SBOMs) | 30 | — | disk + 3 ext |"
            in lines
        )
        assert lines[-1].endswith("updates 0, other commits 0, mixed 0.")
        assert "| at build time | — |" not in lines

    def test_a_build_that_could_not_be_rescanned_is_skipped(self):
        builds = [build("1", "a" * 40, 10), build("2", "b" * 40, None), build("3", "c" * 40, 12)]
        seen = []
        lines = history.render(builds, "amd64", lambda a, b: seen.append((a, b)) or "other")
        assert seen == [("a" * 40, "c" * 40)]
        assert sum(line.startswith("| 2026") for line in lines) == 2

    def test_no_database_date_is_today(self):
        lines = history.render([build("1", "a" * 40, None)], "amd64", lambda a, b: "x")
        assert "vulnerability database of today." in lines[2]
        assert "| same database | — |" in lines


class FakeGhRun:
    """gh run list and gh run download, from fixtures/scan."""

    def __init__(self, runs, with_sbom, with_cve=()):
        self.runs, self.with_sbom, self.with_cve = runs, set(with_sbom), set(with_cve)

    def __call__(self, argv, **_kw):
        if argv[:3] == ["gh", "run", "list"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.runs), "")
        if argv[:3] == ["gh", "run", "download"]:
            run, name, dest = argv[3], argv[argv.index("-n") + 1], Path(argv[argv.index("-D") + 1])
            if name.startswith("sbom-") and run in self.with_sbom:
                shutil.copytree(FIXTURES / "scan" / "in", dest)
                return subprocess.CompletedProcess(argv, 0, "", "")
            if name.startswith("cve-") and run in self.with_cve:
                dest.mkdir(parents=True)
                (dest / "cve-summary.txt").write_text(summary(9).text())
                return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "")


def runs(*ids):
    return [
        {"databaseId": int(i), "headSha": str(i) * 40, "createdAt": f"2026-10-0{i}T00:00:00Z"}
        for i in ids
    ]


class TestHistory:
    @pytest.fixture(autouse=True)
    def fake_grype(self, monkeypatch):
        monkeypatch.setenv("FAKE_GRYPE_REPORTS", str(FIXTURES / "scan" / "grype"))

    def test_the_last_n_with_sboms_oldest_first_rescanned(self, tmp_path):
        gh = Gh(run=FakeGhRun(runs(5, 4, 3, 2, 1), with_sbom={"5", "3", "2", "1"}, with_cve={"3"}))
        builds = history.collect(gh, "amd64", 3, tmp_path, "ci.yml", "main")
        assert [b.run for b in builds] == ["2", "3", "5"]
        assert builds[1].then.all.total == 9
        assert builds[0].then.all is None
        history.rescan(builds, tmp_path, Grype(FIXTURES / "bin" / "grype"), cpes={})
        assert [b.now.all.total for b in builds] == [7, 7, 7]
        assert (tmp_path / "5" / "cve" / "erofs.sbom.json").exists()  # enriched

    def test_the_rescan_applies_todays_not_affected_list(self, tmp_path):
        gh = Gh(run=FakeGhRun(runs(1), with_sbom={"1"}))
        builds = history.collect(gh, "amd64", 3, tmp_path, "ci.yml", "main")
        entries = not_affected.parse("golang.org/x/net GO-2026-3 component_not_present r\n")
        history.rescan(builds, tmp_path, Grype(FIXTURES / "bin" / "grype"), {}, entries)
        vex = json.loads((tmp_path / "1" / "cve" / "erofs.vex.json").read_text())
        assert "not_affected" in [s["status"] for s in vex["statements"]]

    def test_the_rescans_say_nothing(self, tmp_path, monkeypatch, capsys):
        summary_file = tmp_path / "step-summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
        gh = Gh(run=FakeGhRun(runs(1), with_sbom={"1"}))
        builds = history.collect(gh, "amd64", 3, tmp_path / "h", "ci.yml", "main")
        history.rescan(builds, tmp_path / "h", Grype(FIXTURES / "bin" / "grype"), cpes={})
        assert capsys.readouterr().out == ""
        assert not summary_file.exists()

    def test_nothing_to_compare_is_a_note(self, tmp_path):
        gh = Gh(run=FakeGhRun(runs(1), with_sbom=set()))
        with pytest.raises(report.Note, match="no sbom-arm64 artifacts on `main`"):
            history.history(gh, None, "arm64", 3, tmp_path, cpes={}, rep=report.Report.quiet())

    def test_end_to_end(self, tmp_path):
        out = io.StringIO()
        gh = Gh(run=FakeGhRun(runs(2, 1), with_sbom={"2", "1"}, with_cve={"2"}))
        history.history(
            gh, Grype(FIXTURES / "bin" / "grype"), "amd64", 5, tmp_path, cpes={},
            rep=report.Report(stdout=out), label_of=lambda a, b: "update",
        )  # fmt: skip
        text = out.getvalue()
        assert "| 2026-10-01 | `1111111` | — | 7 |  | 2 | — | disk + 1 ext |" in text
        assert "| 2026-10-02 | `2222222` | update | 7 | 0 | 2 | 9 | disk + 1 ext |" in text
        assert "vulnerability database of 2026-11-01." in text
