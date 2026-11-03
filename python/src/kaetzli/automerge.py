"""Merge the pull requests update-packages.yml opened, one at a time, each only after CI
has passed on exactly the commit that lands on main:

    ./tools/auto-merge.py        # what .github/workflows/auto-merge.yml runs
    ./tools/auto-merge.py -n     # say what would happen, touch nothing (works locally)

Green means one thing: the "Package update CI" check, which tools/dispatch-ci.sh writes
from ci.yml's run, concluded `success` on the very commit that is pushed to main, and
nothing else on it failed. Other checks passing is not CI passing, and a neutral or
skipped CI is not a pass. The checks are read together with the commit they belong to,
and a branch that has moved since is not merged.

Green on its own is not enough either, and that is most of this script. A bump's CI ran
against main as it was when the pull request was opened, and the updater opens several
at once — so after the first is merged, every other one was tested against a main that
no longer exists. Each one is therefore rebased onto the current main and built again
before it is merged, unless main has not moved since its last green run. The merge is a
fast-forward push of that tested commit, not a merge button: a rebase or squash merge
writes a new commit, and a merge commit a new tree, neither of which CI has seen. A push
that is not a fast-forward is refused, which is what makes a human pushing to main
mid-run safe — that pull request simply waits for the next run.

A pull request is left alone, for a human, when:
  - it is not from github-actions on an update/ branch — this merges bumps, not changes
  - it carries the `hold` label — the way to say "not this one" without closing it
  - any check on it failed — a red build needs work in build.sh, not a retry
  - a check is still running and started recently — someone else's CI is in flight
  - the rebase conflicts, or the rebuilt CI fails
  - it was labelled `hold` or closed while it was being built: the label and the state
    are read again just before the merge, since a rebuild takes an hour

There is no minimum age. The updater opens pull requests at 17:00 UTC and nothing is
merged before 20:00, which is the time to look and label; a mistake that gets through is
a revert. Closing a pull request skips only that night: update-packages.yml opens it
again the next evening, and `hold` is what keeps it out.

Scheduled runs only act in the evening and at night in Europe: no pull request is
started outside 20:00 to 05:00 UTC (22:00 to 07:00 CEST, 21:00 to 06:00 CET). GitHub's
cron can start a run an hour or more late, so the schedule alone does not guarantee
that — this check does, and it is made before each pull request rather than once,
because one rebuild can take an hour. A pull request started just before the cutoff can
still be building at eight in the morning in Germany; that is the accepted cost of a
window wide enough to survive a late cron. A run started by hand (workflow_dispatch, or
locally) is somebody choosing to act now, and is not held to the window.

Pushes made with GITHUB_TOKEN start no workflow, which applies to main as much as to a
branch: once anything was merged, ci.yml is dispatched on main, so publish-oci and the
caches on main follow what landed.

Every git and gh command is the one the bash version ran, in the same order; the
decisions between them are the functions below.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from kaetzli import repo

NIGHT_START_UTC = 20
NIGHT_END_UTC = 5
HOLD_LABEL = "hold"
CI_CHECK = "Package update CI"
# A check "running" for longer than any CI run takes is one whose reporter died (a
# timed-out run of this script leaves its check in_progress); waiting on it would wait
# forever.
RUNNING_AT_MOST = 6 * 3600
OK = ("SUCCESS", "NEUTRAL", "SKIPPED")


def _epoch(iso: str) -> int:
    """jq's fromdateiso8601: exactly 2026-11-02T01:20:38Z, anything else an error."""
    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp())


def _or(*values):
    """jq's `a // b`: the first that is neither null nor false."""
    for v in values:
        if v is not None and v is not False:
            return v
    return values[-1]


def checks_state(rollup: list[dict], now: int) -> str:
    """green, untested, pending, failed, stale or none, from every check and status on the
    head. Green needs the CI check run itself to have succeeded; all done without that is
    untested."""
    checks = []
    ci = False
    for c in rollup:
        ci = ci or (c.get("name") == CI_CHECK and c.get("conclusion") == "SUCCESS")
        done = (
            _or(c.get("status"), "COMPLETED") == "COMPLETED"
            and _or(c.get("state"), "") != "PENDING"
        )
        result = _or(c.get("conclusion"), c.get("state"), "")
        age = now - _epoch(_or(c.get("startedAt"), "1970-01-01T00:00:00Z"))
        checks.append((done, result, age))
    if not checks:
        return "none"
    if any(done and result not in OK for done, result, _ in checks):
        return "failed"
    if any(not done and age < RUNNING_AT_MOST for done, _, age in checks):
        return "pending"
    if all(done for done, _, _ in checks):
        return "green" if ci else "untested"
    return "stale"


def at_night(event: str, hour: int) -> bool:
    """True when this is not a scheduled run, or the UTC hour is inside the window."""
    return event != "schedule" or hour >= NIGHT_START_UTC or hour < NIGHT_END_UTC


@dataclass
class PullRequest:
    number: int
    branch: str
    labels: list[str]
    url: str


def updates(prs: list[dict]) -> list[PullRequest]:
    """The updater's pull requests, oldest first: update/ branches in this repository."""
    return [
        PullRequest(
            p["number"],
            p["headRefName"],
            [label["name"] for label in p["labels"]],
            p["url"],
        )
        for p in sorted(prs, key=lambda p: p["createdAt"])
        if p["headRefName"].startswith("update/") and not p["isCrossRepository"]
    ]


class Runner:
    """The commands, run in the repository; output not captured is the transcript's."""

    def __init__(self, root: Path, dry_run: bool):
        self.root = root
        self.dry_run = dry_run

    def _run(self, argv: list[str], capture: bool = False) -> subprocess.CompletedProcess:
        sys.stdout.flush()
        return subprocess.run(argv, cwd=self.root, text=True, capture_output=capture)

    def ok(self, *argv: str) -> bool:
        return self._run(list(argv)).returncode == 0

    def out(self, *argv: str) -> str:
        p = self._run(list(argv), capture=True)
        if p.returncode != 0:
            sys.stderr.write(p.stderr)
            raise subprocess.CalledProcessError(p.returncode, argv)
        return p.stdout.rstrip("\n")

    def check(self, *argv: str) -> None:
        p = self._run(list(argv))
        if p.returncode != 0:
            raise subprocess.CalledProcessError(p.returncode, argv)

    def act(self, *argv: str) -> bool:
        """A command that changes something: in a dry run, only said."""
        if self.dry_run:
            say(f"  would run: {' '.join(argv)}")
            return True
        return self.ok(*argv)


def say(line: str) -> None:
    print(line, flush=True)


def vetoed(late: dict) -> str | None:
    """Why a pull request read again just before the merge must not be merged, if it
    must not: closed (or merged by hand) meanwhile, or labelled hold."""
    if late["state"] != "OPEN":
        return f"{late['state'].lower()} while it was built, leaving it"
    if HOLD_LABEL in [label["name"] for label in late["labels"]]:
        return f"labelled {HOLD_LABEL} while it was built, leaving it"
    return None


def merge_one(pr: PullRequest, r: Runner, now: int) -> bool:
    """One pull request, as far as it goes; True when it landed on main."""
    say(f"#{pr.number} {pr.branch}")
    if HOLD_LABEL in pr.labels:
        say(f"  skip: labelled {HOLD_LABEL}")
        return False

    # One call, so the checks and the commit they are for cannot come from two moments.
    view = json.loads(r.out(
        "gh", "pr", "view", str(pr.number), "--json", "headRefOid,statusCheckRollup"
    ))  # fmt: skip
    state = checks_state(view["statusCheckRollup"], now)
    if state == "failed":
        say("  skip: a check failed, needs a human")
        return False
    if state == "pending":
        say("  skip: a check is still running")
        return False

    tracking = f"+refs/heads/{pr.branch}:refs/remotes/origin/{pr.branch}"
    r.check("git", "fetch", "--quiet", "origin", "main", tracking)
    head = r.out("git", "rev-parse", f"origin/{pr.branch}")
    if head != view["headRefOid"]:
        say("  branch moved since its checks were read, leaving it for the next run")
        return False

    if state == "green" and r.ok("git", "merge-base", "--is-ancestor", "origin/main", head):
        say("  green on top of the current main")
    else:
        if r.ok("git", "merge-base", "--is-ancestor", "origin/main", head):
            say(f"  up to date but checks are {state}: building again")
        else:
            say(f"  behind main (checks: {state}): rebasing and building again")
        if r.dry_run:
            say("  would rebase onto origin/main, push, run CI and merge if it passes")
            return False

        r.check("git", "switch", "--quiet", "-C", pr.branch, head)
        if not r.ok("git", "rebase", "--quiet", "origin/main"):
            r.check("git", "rebase", "--abort")
            say(f"::warning::#{pr.number} does not rebase cleanly onto main, needs a human")
            return False
        if r.out("git", "rev-parse", "HEAD") != head:
            # The lease is the head this run looked at: if someone pushed a fix since,
            # this is refused rather than throwing their commit away.
            lease = f"--force-with-lease={pr.branch}:{head}"
            if not r.ok("git", "push", "--quiet", lease, "origin", f"HEAD:{pr.branch}"):
                say(
                    f"::warning::#{pr.number} changed while being rebased,"
                    " leaving it for the next run"
                )
                return False
            head = r.out("git", "rev-parse", "HEAD")

        if not r.ok("./tools/dispatch-ci.sh", pr.branch, pr.url):
            say("  CI failed after the rebase, needs a human")
            return False
        # CI took a while; a fix pushed meanwhile is not the commit that was tested.
        r.check("git", "fetch", "--quiet", "origin", tracking)
        if r.out("git", "rev-parse", f"origin/{pr.branch}") != head:
            say("  branch moved while CI ran, leaving it for the next run")
            return False

    # Read again: a label or a close while it was built is a veto all the same.
    late = json.loads(r.out("gh", "pr", "view", str(pr.number), "--json", "state,labels"))
    if reason := vetoed(late):
        say(f"  {reason}")
        return False

    # Not --force: a fast-forward or nothing, so main gets exactly the tested commit.
    if not r.act("git", "push", "--quiet", "origin", f"{head}:refs/heads/main"):
        say(f"::warning::main moved during this run, #{pr.number} waits for the next one")
        r.check("git", "fetch", "--quiet", "origin", "main")
        return False
    say(f"  merged {r.out('git', 'rev-parse', '--short', head)}")
    return not r.dry_run


def after_merge(pr: PullRequest, r: Runner, sleep: Callable[[float], None]) -> None:
    """GitHub marks the pull request merged once it sees its head on main, asynchronously.
    The branch goes only after that: deleted first, the pull request closes unmerged."""
    r.check("git", "fetch", "--quiet", "origin", "main")
    for _ in range(12):
        if (
            r.out("gh", "pr", "view", str(pr.number), "--json", "state", "--jq", ".state")
            == "MERGED"
        ):
            r.ok("git", "push", "--quiet", "origin", "--delete", pr.branch)
            break
        sleep(5)


def main(
    argv: list[str],
    root: Path | None = None,
    hour: Callable[[], int] = lambda: datetime.now(UTC).hour,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    dry_run = argv[:1] == ["-n"]
    if argv[1 if dry_run else 0 :]:
        print("usage: tools/auto-merge.py [-n]", file=sys.stderr)
        return 2
    root = root or repo.enter_root()
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    now = int(time.time())
    r = Runner(root, dry_run)

    r.check("git", "fetch", "--quiet", "origin", "main")
    prs = updates(json.loads(r.out(
        "gh", "pr", "list", "--state", "open", "--base", "main",
        "--author", "app/github-actions", "--limit", "100",
        "--json", "number,headRefName,createdAt,labels,url,isCrossRepository",
    )))  # fmt: skip

    merged_any = False
    for pr in prs:
        if not at_night(event, hour()):
            say(
                f"outside {NIGHT_START_UTC}:00\u20130{NIGHT_END_UTC}:00 UTC, stopping;"
                " the rest waits for tomorrow night"
            )
            break
        if merge_one(pr, r, now):
            merged_any = True
            after_merge(pr, r, sleep)

    if merged_any:
        r.check("gh", "workflow", "run", "ci.yml", "--ref", "main")
        say("dispatched ci.yml on main")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
