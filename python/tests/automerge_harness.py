"""Scenarios for tools/auto-merge, run through fake git, gh and date (fixtures/automerge).

A scenario is the pull requests, their checks, the refs and what each action does (a
rebase that conflicts, a push that is refused, CI that fails). `materialize` writes it as
the files the fakes read; times are relative to now, so both implementations see the same
ages. The bash version's transcript of each — stdout, exit code, and the log of every git,
gh and dispatch-ci call — is the golden the Python reproduces.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "automerge"
BIN = FIXTURES / "bin"
EXPECTED = FIXTURES / "expected"
BASH = Path(__file__).parents[2] / "tools" / "auto-merge.sh"


def sha(n: int) -> str:
    """A commit id that abbreviates to something recognisable: 0x111 → 1110000…"""
    return f"{n:x}".ljust(40, "0")


MAIN = sha(0xA)


def iso(hours_ago: float, now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - hours_ago * 3600))


def pr(number, branch, age, labels=(), cross=False):
    return {"number": number, "branch": branch, "age": age, "labels": list(labels),
            "cross": cross}  # fmt: skip


CI = "Package update CI"


def check(conclusion="SUCCESS", status="COMPLETED", started=1.0, kind="CheckRun", name="lint"):
    if kind == "StatusContext":
        return {"__typename": kind, "context": name, "state": conclusion, "startedAt": started}
    return {"__typename": kind, "name": name, "status": status, "conclusion": conclusion,
            "startedAt": started}  # fmt: skip


GREEN = [check(name=CI), check(), check(kind="StatusContext")]

SCENARIOS = {
    # Left alone: held, red, running; and two gh lists that are not updates.
    "skips": {
        "prs": [
            pr(1, "update/held-1.0", 30.5, ["hold", "dependencies"]),
            pr(3, "update/red-1.0", 40.5),
            pr(4, "update/running-1.0", 50.5),
            pr(5, "feature/not-an-update", 60.5),
            pr(6, "update/from-a-fork", 70.5, cross=True),
        ],
        "checks": {
            3: [check(), check("FAILURE")],
            4: [check(name=CI), check("", status="IN_PROGRESS", started=0.5)],
        },
    },
    # Green on the current main: merged as it is, however young; GitHub notices the merge
    # on the second look, the branch is deleted, and CI runs on main.
    "green": {
        "prs": [pr(10, "update/a-2.0", 0.5)],
        "checks": {10: GREEN},
        "refs": {"origin/update/a-2.0": sha(0x10)},
        "ancestors": [sha(0x10)],
        "state": {10: ["OPEN", "MERGED"]},
    },
    # Behind main: rebased, pushed with a lease, built again, merged. The second was
    # green on the old main and is rebased in turn, but main has moved by then.
    "rebase": {
        "prs": [pr(11, "update/b-2.0", 40.5), pr(12, "update/c-2.0", 30.5)],
        "checks": {11: GREEN, 12: GREEN},
        "refs": {"origin/update/b-2.0": sha(0x11), "origin/update/c-2.0": sha(0x12)},
        "ancestors": [sha(0x12)],
        "rebase": {"update/b-2.0": sha(0x111)},
        "dispatch": {"update/b-2.0": ["success"]},
        "push_fails": [f"origin {sha(0x12)}:refs/heads/main"],
    },
    # Everything that sends a pull request back to a human after the checks passed.
    "failures": {
        "prs": [
            pr(20, "update/conflict-1", 80.5),
            pr(21, "update/lease-1", 70.5),
            pr(22, "update/red-ci-1", 60.5),
            pr(23, "update/moved-1", 50.5),
        ],
        "checks": {n: GREEN for n in (20, 21, 22, 23)},
        "refs": {
            f"origin/update/{b}-1": sha(n)
            for b, n in (("conflict", 0x20), ("lease", 0x21), ("red-ci", 0x22), ("moved", 0x23))
        },
        "rebase": {
            "update/conflict-1": "conflict",
            "update/lease-1": sha(0x211),
            "update/red-ci-1": sha(0x221),
            "update/moved-1": sha(0x231),
        },
        "push_fails": [
            "--force-with-lease=update/lease-1:" + sha(0x21) + " origin HEAD:update/lease-1"
        ],
        "dispatch": {
            "update/red-ci-1": ["failure"],
            "update/moved-1": ["success", "move " + sha(0x232)],
        },
    },
    # Checks that are neither green nor red: none at all on a branch already on main
    # (built again, then merged), and one whose reporter died hours ago on a branch the
    # rebase leaves as it is (nothing to push; built again, merged).
    "unsure": {
        "prs": [pr(24, "update/no-checks-1", 30.5), pr(25, "update/dead-check-1", 31.5)],
        "checks": {
            24: [],
            25: [check(), check("", status="IN_PROGRESS", started=8.0)],
        },
        "refs": {"origin/update/no-checks-1": sha(0x24), "origin/update/dead-check-1": sha(0x25)},
        "ancestors": [sha(0x24)],
        "dispatch": {"update/no-checks-1": ["success"], "update/dead-check-1": ["success"]},
    },
    # Green is CI's own check passing on that commit, not every check there passing:
    # without it (only other checks, or CI neutral) the pull request is built again.
    "untested": {
        "prs": [pr(26, "update/no-ci-1", 30.5), pr(27, "update/neutral-ci-1", 31.5)],
        "checks": {
            26: [check(), check(kind="StatusContext")],
            27: [check("NEUTRAL", name=CI), check()],
        },
        "refs": {"origin/update/no-ci-1": sha(0x26), "origin/update/neutral-ci-1": sha(0x27)},
        "ancestors": [sha(0x26), sha(0x27)],
        "dispatch": {"update/no-ci-1": ["success"], "update/neutral-ci-1": ["success"]},
    },
    # The checks were read for one commit and the branch is now another: not merged.
    "moved": {
        "prs": [pr(28, "update/pushed-1", 30.5)],
        "checks": {28: GREEN},
        "head_seen": {28: sha(0x28)},
        "refs": {"origin/update/pushed-1": sha(0x281)},
        "ancestors": [sha(0x28), sha(0x281)],
    },
    # A veto while it was built counts: labelled hold, or closed, before the merge.
    "veto": {
        "prs": [pr(40, "update/held-late-1", 30.5), pr(41, "update/closed-late-1", 31.5)],
        "checks": {40: GREEN, 41: GREEN},
        "refs": {"origin/update/held-late-1": sha(0x40), "origin/update/closed-late-1": sha(0x41)},
        "rebase": {"update/held-late-1": sha(0x401), "update/closed-late-1": sha(0x411)},
        "dispatch": {"update/held-late-1": ["success"], "update/closed-late-1": ["success"]},
        "late": {40: ("OPEN", ["hold"]), 41: ("CLOSED", [])},
    },
    # A scheduled run at noon UTC starts nothing, nor at 19:00; at 20:00, 23:00 and 04:00
    # it does.
    "daytime": {
        "prs": [pr(30, "update/d-1", 30.5)],
        "checks": {30: GREEN},
        "event": "schedule",
        "hour": 12,
    },
    "evening-19": {
        "prs": [pr(34, "update/h-1", 30.5)],
        "checks": {34: GREEN},
        "event": "schedule",
        "hour": 19,
    },
    "evening-20": {
        "prs": [pr(35, "update/i-1", 30.5)],
        "checks": {35: [check("FAILURE")]},
        "event": "schedule",
        "hour": 20,
    },
    "night-23": {
        "prs": [pr(31, "update/e-1", 30.5)],
        "checks": {31: [check("FAILURE")]},
        "event": "schedule",
        "hour": 23,
    },
    "night-04": {
        "prs": [pr(32, "update/f-1", 30.5)],
        "checks": {32: [check("FAILURE")]},
        "event": "schedule",
        "hour": 4,
    },
    "morning-05": {
        "prs": [pr(33, "update/g-1", 30.5)],
        "checks": {33: GREEN},
        "event": "schedule",
        "hour": 5,
    },
}


def materialize(name: str, fake: Path, now: float) -> dict:
    """The scenario's files for the fakes; returns the environment to run it in."""
    s = SCENARIOS[name]
    fake.mkdir(parents=True, exist_ok=True)
    prs = [
        {"number": p["number"], "headRefName": p["branch"], "createdAt": iso(p["age"], now),
         "labels": [{"name": n} for n in p["labels"]],
         "url": f"https://github.com/o/r/pull/{p['number']}", "isCrossRepository": p["cross"]}
        for p in s["prs"]
    ]  # fmt: skip
    (fake / "prs.json").write_text(json.dumps(prs))
    refs = {"origin/main": MAIN, **s.get("refs", {})}
    for number, checks in s.get("checks", {}).items():
        rollup = [{**c, "startedAt": iso(c["startedAt"], now)} for c in checks]
        branch = next(p["branch"] for p in s["prs"] if p["number"] == number)
        head = s.get("head_seen", {}).get(number) or refs.get(f"origin/{branch}", sha(number))
        (fake / f"checks.{number}.json").write_text(
            json.dumps({"headRefOid": head, "statusCheckRollup": rollup})
        )
    for p in s["prs"]:
        state, labels = s.get("late", {}).get(p["number"], ("OPEN", p["labels"]))
        late = {"state": state, "labels": [{"name": n} for n in labels]}
        (fake / f"late.{p['number']}.json").write_text(json.dumps(late))
    (fake / "refs").write_text("".join(f"{k} {v}\n" for k, v in refs.items()))
    (fake / "ancestors").write_text("".join(f"origin/main {h}\n" for h in s.get("ancestors", [])))
    for branch, result in s.get("rebase", {}).items():
        (fake / f"rebase.{branch.replace('/', '%')}").write_text(result + "\n")
    (fake / "push-fails").write_text("".join(f"{p}\n" for p in s.get("push_fails", [])))
    for branch, lines in s.get("dispatch", {}).items():
        (fake / f"dispatch.{branch.replace('/', '%')}").write_text("".join(f"{x}\n" for x in lines))
    for number, states in s.get("state", {}).items():
        (fake / f"state.{number}").write_text("".join(f"{x}\n" for x in states))
    (fake / "log").write_text("")
    env = {"FAKE": str(fake), "GITHUB_EVENT_NAME": s.get("event", "workflow_dispatch")}
    if "hour" in s:
        env["FAKE_HOUR"] = str(s["hour"])
    return env


def checkout(tmp: Path) -> Path:
    """A directory standing in for the repository: tools/dispatch-ci.sh is the fake, and
    tools/auto-merge.sh the real one, which cds to the directory above its own."""
    root = tmp / "repo"
    (root / "tools").mkdir(parents=True)
    shutil.copy(FIXTURES / "dispatch-ci.sh", root / "tools" / "dispatch-ci.sh")
    shutil.copy(BASH, root / "tools" / "auto-merge.sh")
    return root


def normalize(text: str, fake: Path) -> str:
    return text.replace(str(fake), "$FAKE")
