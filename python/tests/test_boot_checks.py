"""kaetzli.boot.checks, and the lines test/gardener.py and test/etc-upgrade.py type with it."""

from __future__ import annotations

import re
import subprocess

import pytest

from kaetzli.boot import checks, etc_upgrade, gardener


def bash(script: str) -> str:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout


def test_all_holding_is_ok():
    assert bash(checks.line([("a", "true"), ("b", "true")], "T")) == "T-OK|\n"


def test_the_first_failure_is_named_and_ends_the_line():
    out = bash(checks.line([("a", "true"), ("b fails", "false"), ("c", "false")], "T"))
    assert out == "T-FAIL: b fails|\n"


def test_the_typed_line_cannot_match_its_own_answer():
    typed = checks.line([("b fails", "false")], "T")
    assert not re.search(r"T-(OK|FAIL: [^|\r\n]*)\|", typed)


@pytest.mark.parametrize("what", ["the /etc's file", "a | b"])
def test_a_message_that_would_break_the_line_is_refused(what):
    with pytest.raises(ValueError, match="quote or a"):
        checks.line([(what, "true")], "T")


@pytest.mark.parametrize(
    "typed",
    [gardener.CHECK, gardener.SECOND_CHECK, etc_upgrade.FIRST_CHECK, etc_upgrade.SECOND_CHECK],
)
def test_each_typed_line_is_one_complete_bash_command(typed):
    assert subprocess.run(["bash", "-n", "-c", typed]).returncode == 0


def test_the_upgrade_changes_two_files_and_adds_one(tmp_path):
    for version in (1, 2):
        etc_upgrade.trees(tmp_path, version)
    v1 = tmp_path / "confext-1/etc/kaetzli-upgrade"
    v2 = tmp_path / "confext-2/etc/kaetzli-upgrade"
    assert sorted(p.name for p in v1.iterdir()) == ["followed", "written"]
    assert sorted(p.name for p in v2.iterdir()) == ["added", "followed", "written"]
    assert (v1 / "followed").read_text() != (v2 / "followed").read_text()
    assert (tmp_path / "sysext-2/usr/share/kaetzli-upgrade").read_text() == "2\n"
