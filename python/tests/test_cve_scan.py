"""test/cve-scan.py end to end, against a stand-in grype.

fixtures/scan/expected/ is what the bash test/cve-scan.sh printed and wrote for
fixtures/scan/in/ with that stand-in, except the VEX status notes, which name the package
now (the bash version left it empty), and the not-affected column and list, which came after.
"""

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kaetzli import repo, report
from kaetzli.cve import grype, not_affected, scan

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = FIXTURES / "scan" / "expected"


@pytest.fixture
def build(tmp_path):
    d = tmp_path / "output"
    shutil.copytree(FIXTURES / "scan" / "in", d)
    return d


@pytest.fixture
def fake_grype(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_GRYPE_REPORTS", str(FIXTURES / "scan" / "grype"))
    monkeypatch.setenv("FAKE_GRYPE_LOG", str(tmp_path / "grype.log"))
    return grype.Grype(FIXTURES / "bin" / "grype")


def run_scan(build, g, **kw):
    out = io.StringIO()
    scan.scan(build, g, report.Report(stdout=out), arch="amd64", **kw)
    return out.getvalue()


def test_the_report_is_the_bash_versions(build, fake_grype, tmp_path):
    assert run_scan(build, fake_grype) == (EXPECTED / "stdout.md").read_text()
    assert (build / "cve-summary.txt").read_text() == (EXPECTED / "cve-summary.txt").read_text()
    assert (tmp_path / "grype.log").read_text() == (EXPECTED / "grype.log").read_text()


def test_vex_documents(build, fake_grype):
    run_scan(build, fake_grype)
    for name in ("erofs", "oci"):
        got = (build / "cve" / f"{name}.vex.json").read_text()
        assert got == (EXPECTED / f"{name}.vex.json").read_text()
    assert not (build / "cve" / "ext-podman.vex.json").exists()


UNAFFECTED = """\
golang.org/x/net GO-2026-3 vulnerable_code_not_present the http2 server is not linked
tar CVE-2026-99 vulnerable_code_not_present a finding grype no longer reports
"""


def test_not_affected_lines_are_vex_statements_and_stale_ones_are_listed(build, fake_grype):
    entries = not_affected.parse(UNAFFECTED, "test/cve-not-affected.txt")
    out = run_scan(build, fake_grype, unaffected=entries)
    vex = json.loads((build / "cve" / "erofs.vex.json").read_text())
    mine = [s for s in vex["statements"] if s["status"] == "not_affected"]
    assert mine == [
        {
            "vulnerability": {"name": "GO-2026-3"},
            "products": [{"@id": "pkg:golang/golang.org/x/net@v0.57.0"}],
            "status": "not_affected",
            "justification": "vulnerable_code_not_present",
            "impact_statement": "the http2 server is not linked (test/cve-not-affected.txt:1)",
        }
    ]
    assert "| `kernel` | 7.3.1 | 1 |" in out
    assert "- line 2: `tar` CVE-2026-99" in out
    assert "GO-2026-3" not in out.split("Stale in")[1]


def test_grype_reports_are_kept(build, fake_grype):
    run_scan(build, fake_grype)
    names = sorted(p.name for p in (build / "cve").iterdir())
    assert names == ["erofs.json", "erofs.vex.json", "ext-podman.json", "oci.json", "oci.vex.json"]


def test_enrich_writes_the_sbom_it_scanned(build, fake_grype, tmp_path):
    cpes = {"gawk": ["cpe:2.3:a:gnu:gawk:9:*:*:*:*:*:*:*"]}
    run_scan(build, fake_grype, cpes=cpes)
    scanned = json.loads((build / "cve" / "erofs.sbom.json").read_text())
    gawk = next(p for p in scanned["packages"] if p["name"] == "gawk")
    assert gawk["externalRefs"][-1]["referenceLocator"] == "cpe:2.3:a:gnu:gawk:5.3.1:*:*:*:*:*:*:*"
    assert "grype -q erofs.sbom.json" in (tmp_path / "grype.log").read_text()


def test_a_failed_sbom_is_left_out(build, fake_grype, monkeypatch, tmp_path, capsys):
    reports = tmp_path / "reports"
    shutil.copytree(FIXTURES / "scan" / "grype", reports)
    (reports / "ext-podman.json").unlink()
    monkeypatch.setenv("FAKE_GRYPE_REPORTS", str(reports))
    out = run_scan(build, fake_grype)
    assert "`ext-podman`" not in out
    assert not (build / "cve" / "ext-podman.json").exists()
    assert "grype failed on" in capsys.readouterr().err


def test_no_sboms_is_a_note(tmp_path, fake_grype):
    with pytest.raises(report.Note, match="no SBOMs"):
        scan.scan(tmp_path, fake_grype, report.Report.quiet(), arch="amd64")


def test_an_unreachable_database_is_a_note(build):
    g = grype.Grype("/bin/false")
    with pytest.raises(report.Note, match="could not update"):
        scan.scan(build, g, report.Report.quiet(), arch="amd64")


def test_the_entry_point(build, tmp_path):
    """./test/cve-scan.py, through launch.py's venv, with the stand-in on PATH."""
    env = dict(
        os.environ,
        PATH=f"{FIXTURES / 'bin'}:{os.environ['PATH']}",
        FAKE_GRYPE_REPORTS=str(FIXTURES / "scan" / "grype"),
        GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"),
    )
    env.pop("GRYPE_PINNED", None)
    p = subprocess.run(
        [sys.executable, repo.ROOT / "test" / "cve-scan.py", build],
        env=env, capture_output=True, text=True, cwd=tmp_path, check=False,
    )  # fmt: skip
    assert p.returncode == 0, p.stderr
    assert "| **disk image + extensions** | 7 |" in p.stdout
    assert (tmp_path / "summary.md").read_text() == p.stdout
