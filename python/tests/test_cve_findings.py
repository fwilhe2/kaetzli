from pathlib import Path

import pytest

from kaetzli.cve import findings
from kaetzli.cve.summary import Counts, Summary

FIXTURES = Path(__file__).parent / "fixtures" / "scan"


@pytest.fixture
def report():
    return findings.load(FIXTURES / "report.json")


class TestCounts:
    def test_each_cve_once_per_package_and_version(self, report):
        c = findings.counts(report)
        # CVE-2026-1/openssh is listed twice and counted as its first listing (Medium,
        # no fix); severity missing is "other", as is Negligible.
        assert c == Counts(
            total=6,
            critical=1,
            high=1,
            medium=1,
            low=1,
            other=2,
            fixable=2,
            patched=2,
            not_affected=1,
        )

    def test_patched_is_what_vex_closed_only(self, report):
        # CVE-2026-6 twice, CVE-2026-7; CVE-2026-8 was ignored by another rule, and
        # CVE-2016-0774 by a not_affected statement, which is not a patch.
        assert findings.counts(report).patched == 2

    def test_not_affected_is_its_own_count(self, report):
        assert findings.not_affected(report) == [("kernel", "7.3.1", 1)]

    def test_an_empty_report(self):
        assert findings.counts(findings.Report([], [])) == Counts()

    def test_merged_reports(self, report):
        both = findings.merge([report, report])
        assert findings.counts(both) == findings.counts(report)
        assert len(both.matches) == 2 * len(report.matches)


class TestMostAffected:
    def test_packages_by_distinct_cves(self, report):
        assert findings.most_affected(report) == [
            ("kernel", "7.3.1", 3),
            ("golang.org/x/net", "v0.57.0", 1),
            ("openssh", "10.6p1", 1),
            ("tar", "1.35", 1),
        ]

    def test_at_most_n(self, report):
        assert len(findings.most_affected(report, n=2)) == 2


class TestPatched:
    def test_by_package_then_id_with_severity(self, report):
        assert findings.patched(report) == [
            ("CVE-2026-7", "Unknown", "bash", "5.3"),
            ("CVE-2026-6", "Medium", "glibc", "2.44"),
        ]


class TestSummaryLine:
    def test_round_trip(self):
        c = Counts(
            total=94,
            critical=1,
            high=27,
            medium=59,
            low=7,
            other=0,
            fixable=4,
            patched=8,
            not_affected=90,
        )
        line = c.line()
        assert line == (
            "total 94 critical 1 high 27 medium 59 low 7 other 0 fixable 4 patched 8"
            " not_affected 90"
        )
        assert Counts.parse(line) == c

    def test_a_line_from_before_not_affected_parses_with_zero(self):
        old = "total 94 critical 1 high 27 medium 59 low 7 other 0 fixable 4 patched 8"
        assert Counts.parse(old).not_affected == 0

    def test_parse_tolerates_missing_keys(self):
        assert Counts.parse("total 3 fixable 1") == Counts(total=3, fixable=1)
        assert Counts.parse("") is None


class TestSummaryFile:
    TEXT = (
        "db 2026-11-01 grype-0.120.1\n"
        "doc erofs total 2 critical 0 high 1 medium 1 low 0 other 0 fixable 1 patched 0"
        " not_affected 4\n"
        "doc ext-podman total 1 critical 1 high 0 medium 0 low 0 other 0 fixable 1 patched 0"
        " not_affected 0\n"
        "all total 3 critical 1 high 1 medium 1 low 0 other 0 fixable 2 patched 0"
        " not_affected 4\n"
    )

    def test_parse(self):
        s = Summary.parse(self.TEXT)
        assert s.db == "2026-11-01"
        assert s.grype == "grype-0.120.1"
        assert [name for name, _ in s.docs] == ["erofs", "ext-podman"]
        assert s.all.total == 3
        assert s.extensions == 1

    def test_write_is_the_same_text(self):
        assert Summary.parse(self.TEXT).text() == self.TEXT

    def test_missing_file(self, tmp_path):
        s = Summary.read(tmp_path / "nope.txt")
        assert s.all is None
        assert s.docs == []
