import pytest

from kaetzli import repo
from kaetzli.cve import findings, not_affected

GOOD = """\
# a comment, then a blank line

kernel CVE-2016-0774 vulnerable_code_not_present fixed long ago; NVD has no range
golang.org/x/net GO-2026-6603 vulnerable_code_not_present net/http2 server, not linked
"""


def entries():
    return not_affected.parse(GOOD, "f.txt")


class TestParse:
    def test_fields_and_the_reason_is_the_rest_of_the_line(self):
        e = entries()
        assert [(x.package, x.vulnerability, x.justification) for x in e] == [
            ("kernel", "CVE-2016-0774", "vulnerable_code_not_present"),
            ("golang.org/x/net", "GO-2026-6603", "vulnerable_code_not_present"),
        ]
        assert e[0].reason == "fixed long ago; NVD has no range"
        assert e[0].line == 3

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("kernel CVE-2016-0774 vulnerable_code_not_present\n", "a reason"),
            ("kernel CVE-2016-0774 probably_fine because\n", "not an OpenVEX justification"),
            ("kernel 2016-0774 component_not_present x\n", "not a vulnerability id"),
            (
                "kernel CVE-2026-1 component_not_present x\n"
                "kernel CVE-2026-1 component_not_present y\n",
                "listed twice",
            ),
        ],
    )
    def test_a_broken_line_names_its_line_and_what_is_wrong(self, text, message):
        with pytest.raises(ValueError, match=message) as e:
            not_affected.parse(text, "f.txt")
        assert str(e.value).startswith("f.txt:")


SBOM = {
    "packages": [
        {
            "name": "kernel",
            "externalRefs": [
                {"referenceType": "purl", "referenceLocator": "pkg:generic/kernel@7.3.5"},
                {"referenceType": "cpe23Type", "referenceLocator": "cpe:2.3:o:linux:..."},
            ],
        },
        {
            "name": "golang.org/x/net",
            "externalRefs": [
                {"referenceType": "purl", "referenceLocator": "pkg:golang/golang.org/x/net@v0.57.0"}
            ],
        },
        {
            "name": "golang.org/x/net",
            "externalRefs": [
                {"referenceType": "purl", "referenceLocator": "pkg:golang/golang.org/x/net@v0.61.0"}
            ],
        },
        {"name": "tar", "externalRefs": []},
    ]
}


class TestStatements:
    def test_one_per_entry_and_purl_whatever_the_version(self):
        s = not_affected.statements(SBOM, entries())
        assert [(x["vulnerability"]["name"], x["products"][0]["@id"]) for x in s] == [
            ("CVE-2016-0774", "pkg:generic/kernel@7.3.5"),
            ("GO-2026-6603", "pkg:golang/golang.org/x/net@v0.57.0"),
            ("GO-2026-6603", "pkg:golang/golang.org/x/net@v0.61.0"),
        ]

    def test_openvex_keys_and_where_the_claim_comes_from(self):
        s = not_affected.statements(SBOM, entries())[0]
        assert list(s) == [
            "vulnerability",
            "products",
            "status",
            "justification",
            "impact_statement",
        ]
        assert s["status"] == "not_affected"
        assert s["justification"] == "vulnerable_code_not_present"
        assert s["impact_statement"] == "fixed long ago; NVD has no range (f.txt:3)"

    def test_none_for_a_package_the_sbom_does_not_have(self):
        assert not_affected.statements({"packages": [{"name": "tar"}]}, entries()) == []


def match(vid, pkg, related=()):
    return {
        "vulnerability": {"id": vid},
        "relatedVulnerabilities": [{"id": r} for r in related],
        "artifact": {"name": pkg, "version": "1"},
    }


class TestStale:
    def test_an_entry_grype_still_reports_open_or_closed_is_not_stale(self):
        r = findings.Report(
            [match("CVE-2016-0774", "kernel")], [match("GO-2026-6603", "golang.org/x/net")]
        )
        assert not_affected.stale(entries(), [r]) == []

    def test_an_entry_nothing_matches_is(self):
        r = findings.Report([match("CVE-2016-0774", "openssh")], [])
        assert [e.vulnerability for e in not_affected.stale(entries(), [r])] == [
            "CVE-2016-0774",
            "GO-2026-6603",
        ]

    def test_a_related_id_counts(self):
        # grype names a Go finding by its GO- or GHSA- id, with the CVE as related.
        e = not_affected.parse("x/y CVE-2026-1 vulnerable_code_not_present r\n", "f")
        r = findings.Report([match("GHSA-aaaa", "x/y", related=["CVE-2026-1"])], [])
        assert not_affected.stale(e, [r]) == []


class TestTheRepositorysList:
    def test_it_parses(self):
        assert not_affected.load(repo.ROOT / not_affected.PATH)

    def test_every_package_is_one_of_ours_or_a_go_module(self):
        for e in not_affected.load(repo.ROOT / not_affected.PATH):
            assert "/" in e.package or (repo.ROOT / "packages" / e.package).is_dir(), e
