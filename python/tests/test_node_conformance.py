"""test/node-conformance.py's verdict against the bash version's.

Each fixtures/node-conformance/<case>/ is a ginkgo run.log and the status ssh returned;
expected/ is what test/node-conformance.sh's closing block printed and returned for it
outside CI, expected-github/ inside (GITHUB_ACTIONS set), with the job summary it wrote.
The boot and the suite need KVM and an hour; they are checked live, not here.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from kaetzli.boot import node_conformance as nc

FIXTURES = Path(__file__).parent / "fixtures" / "node-conformance"
CASES = sorted(d.name for d in FIXTURES.iterdir() if d.is_dir())


@pytest.mark.parametrize("github", [False, True], ids=["local", "github"])
@pytest.mark.parametrize("case", CASES)
def test_the_verdict_is_the_bash_versions(case, github, tmp_path, capsys):
    fixture = FIXTURES / case
    expected = fixture / ("expected-github" if github else "expected")
    summary = tmp_path / "summary"
    summary.touch()
    env = {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary)} if github else {}
    out = io.StringIO()
    v = nc.verdict((fixture / "run.log").read_text())
    rc = nc.conclude(v, "amd64", int((fixture / "status").read_text()), env=env, out=out)
    assert (out.getvalue(), capsys.readouterr().err, rc) == (
        (expected / "stdout").read_text(),
        (expected / "stderr").read_text(),
        int((expected / "rc").read_text()),
    )
    if github:
        assert summary.read_text() == (expected / "summary").read_text()


def test_a_summary_range_runs_to_the_end_without_a_ran_line():
    log = "Summarizing 1 Failure:\n  [FAIL] one\n  here.go:1\n  [PANICKED] two\n"
    assert nc.verdict(log) == nc.Verdict("", "", ["[FAIL] one", "[PANICKED] two"])


def test_failures_outside_the_summary_are_not_listed():
    log = "  [FAIL] early\nSummarizing 1 Failure:\n  [FAIL] one\nRan 1 of 2 Specs\n  [FAIL] late\n"
    assert nc.verdict(log).failed == ["[FAIL] one"]
