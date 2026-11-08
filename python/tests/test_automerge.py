import json
import subprocess

import pytest

from kaetzli import automerge
from kaetzli.automerge import checks_state


def run_(name="x", status="COMPLETED", conclusion="SUCCESS"):
    return {"name": name, "status": status, "conclusion": conclusion}


CI = "Package update CI"


@pytest.mark.parametrize(
    "rollup, state",
    [
        ([run_(CI), run_("result")], "green"),
        ([run_(CI), run_("lint", conclusion="SKIPPED")], "green"),
        ([run_(CI), run_("result", conclusion="FAILURE")], "failed"),
        ([run_(CI), run_("result", status="IN_PROGRESS", conclusion="")], "pending"),
        ([run_(CI), {"context": "ext", "state": "PENDING"}], "pending"),
        ([run_(CI), {"context": "ext", "state": "ERROR"}], "failed"),
        ([run_("result")], "untested"),
        # ci.yml's update-check job completes the CI check and starts auto-merge, so it is
        # still running when auto-merge looks: it cannot be what holds the merge back
        ([run_(CI), run_("update-check", status="IN_PROGRESS", conclusion="")], "green"),
        ([], "untested"),
        # a failure wins over a running check: it needs a human either way
        ([run_("a", conclusion="FAILURE"), run_("b", status="QUEUED", conclusion="")], "failed"),
    ],
)
def test_checks_state(rollup, state):
    assert checks_state(rollup) == state


PRS = [
    {"number": 238, "headRefName": "update/glibc", "labels": []},
    {"number": 233, "headRefName": "update/expat", "labels": []},
    {"number": 240, "headRefName": "update/curl", "labels": [{"name": "hold"}]},
    {"number": 241, "headRefName": "feature/x", "labels": []},
    {"number": 234, "headRefName": "update/cri-o", "labels": []},
]
ROLLUPS = {
    233: [run_(CI, conclusion="FAILURE")],
    234: [run_(CI)],
    238: [run_(CI)],
}
FILES = {
    233: ["packages/expat/env.sh"],
    234: ["packages/cri-o/env.sh"],
    238: ["packages/glibc/env.sh", "packages/glibc/patches/upstream/0001-x.patch"],
}


@pytest.mark.parametrize(
    "files, outside",
    [
        (["packages/curl/env.sh"], []),
        (["packages/bash/env.sh", "packages/bash/patches/upstream/bash53-004"], []),
        (["packages/bash/patches/upstream/bash53-005"], []),
        (["builder/Containerfile", "image/Containerfile"], []),
        # what a bump never touches
        (["packages/curl/env.sh", ".github/workflows/ci.yml"], [".github/workflows/ci.yml"]),
        (["packages/curl/build.sh"], ["packages/curl/build.sh"]),
        (["packages/curl/patches/ours.patch"], ["packages/curl/patches/ours.patch"]),
        # a signing key is a person's decision (tools/pin-upstream-key.sh), never a bot's
        (
            ["packages/curl/env.sh", "packages/curl/upstream-key.asc"],
            ["packages/curl/upstream-key.asc"],
        ),
        # one package per bump, and a snapshot bump is a bump of nothing else
        (["packages/curl/env.sh", "packages/zlib/env.sh"], ["packages/zlib/env.sh"]),
        (["builder/Containerfile", "packages/curl/env.sh"], ["packages/curl/env.sh"]),
        ([], ["(no files)"]),
        (["packages/curl/env.sh"] * 100, ["(100 files or more: gh lists no more)"]),
    ],
)
def test_outside_scope(files, outside):
    assert automerge.outside_scope(files) == outside


class FakeGh:
    def __init__(self, refuse=(), files=None):
        self.calls = []
        self.refuse = set(refuse)
        self.files = FILES | (files or {})

    def __call__(self, argv):
        self.calls.append(argv)
        out, rc = "", 0
        if argv[:3] == ["gh", "pr", "list"]:
            out = json.dumps(PRS)
        elif argv[:3] == ["gh", "pr", "view"]:
            n = int(argv[3])
            files = [{"path": f} for f in self.files[n]]
            out = json.dumps({"statusCheckRollup": ROLLUPS[n], "files": files})
        elif argv[:3] == ["gh", "pr", "merge"] and int(argv[3]) in self.refuse:
            rc = 1
        return subprocess.CompletedProcess(argv, rc, out, "")

    def acted(self):
        acting = (["gh", "pr", "merge"], ["gh", "workflow", "run"])
        return [c for c in self.calls if c[:3] in acting]


def test_merges_green_in_order_then_dispatches_ci(capsys):
    gh = FakeGh()
    assert automerge.main([], run=gh) == 0
    assert gh.acted() == [
        ["gh", "pr", "merge", "234", "--rebase", "--delete-branch"],
        ["gh", "pr", "merge", "238", "--rebase", "--delete-branch"],
        ["gh", "workflow", "run", "ci.yml", "--ref", "main"],
    ]
    out = capsys.readouterr().out
    assert "#233: failed" in out
    assert "240" not in out and "241" not in out


def test_dry_run_touches_nothing(capsys):
    gh = FakeGh()
    assert automerge.main(["-n"], run=gh) == 0
    assert gh.acted() == []
    assert capsys.readouterr().out.count("would merge") == 2


def test_refused_merge_warns_and_goes_on(capsys):
    gh = FakeGh(refuse={234})
    assert automerge.main([], run=gh) == 0
    assert gh.acted()[-1] == ["gh", "workflow", "run", "ci.yml", "--ref", "main"]
    assert "::warning::#234 did not merge" in capsys.readouterr().out


def test_nothing_merged_dispatches_nothing():
    gh = FakeGh(refuse={234, 238})
    automerge.main([], run=gh)
    assert not any(c[:3] == ["gh", "workflow", "run"] for c in gh.calls)


def test_usage():
    assert automerge.main(["--bogus"], run=FakeGh()) == 2


def test_a_green_pull_request_that_changes_more_than_a_bump_is_left(capsys):
    gh = FakeGh(files={234: ["packages/cri-o/env.sh", "packages/cri-o/build.sh"]})
    assert automerge.main([], run=gh) == 0
    assert gh.acted() == [
        ["gh", "pr", "merge", "238", "--rebase", "--delete-branch"],
        ["gh", "workflow", "run", "ci.yml", "--ref", "main"],
    ]
    out = capsys.readouterr().out
    assert "#234: changes more than a bump" in out
    assert "packages/cri-o/build.sh" in out
