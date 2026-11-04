"""Merge the green pull requests update-packages.yml opened (tools/auto-merge.py).

Green means the "Package update CI" check passed and no other check failed or is still
running. A pull request labelled `hold` is left alone. Each is merged as it is, without
rebasing it onto the current main and building it again: one bump rarely affects
another, CI runs on main after the merges, and a red main does not release. A pull
request that no longer merges cleanly is GitHub's to refuse and a human's to fix.

When is the workflow's cron (.github/workflows/auto-merge.yml), not this module.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable

CI_CHECK = "Package update CI"
HOLD_LABEL = "hold"
PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def checks_state(rollup: list[dict]) -> str:
    """green, pending, failed or untested, from a pull request's statusCheckRollup:
    check runs (status, conclusion) and commit statuses (state) alike."""
    checks = []
    for c in rollup:
        done = (c.get("status") or "COMPLETED") == "COMPLETED" and c.get("state") != "PENDING"
        result = c.get("conclusion") or c.get("state") or ""
        checks.append((c.get("name") or c.get("context"), done, result))
    if any(done and result not in PASSING for _, done, result in checks):
        return "failed"
    if any(not done for _, done, _ in checks):
        return "pending"
    if any(name == CI_CHECK and result == "SUCCESS" for name, _, result in checks):
        return "green"
    return "untested"


def _gh_json(run: Runner, argv: list[str]):
    p = run(argv)
    if p.returncode != 0:
        raise SystemExit(f"{' '.join(argv)} failed: {p.stderr.strip()}")
    return json.loads(p.stdout)


def update_prs(run: Runner) -> list[int]:
    """Open update/ pull requests from github-actions, not on hold, oldest first."""
    prs = _gh_json(run, [
        "gh", "pr", "list", "--state", "open", "--base", "main",
        "--author", "app/github-actions", "--limit", "100",
        "--json", "number,headRefName,labels",
    ])  # fmt: skip
    return sorted(
        p["number"]
        for p in prs
        if p["headRefName"].startswith("update/")
        and HOLD_LABEL not in {label["name"] for label in p["labels"]}
    )


def main(argv: list[str] | None = None, run: Runner = _run) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv not in ([], ["-n"]):
        print("usage: auto-merge.py [-n]", file=sys.stderr)
        return 2
    dry_run = argv == ["-n"]

    merged_any = False
    for pr in update_prs(run):
        view = _gh_json(run, ["gh", "pr", "view", str(pr), "--json", "statusCheckRollup"])
        state = checks_state(view["statusCheckRollup"])
        print(f"#{pr}: {state}", flush=True)
        if state != "green":
            continue
        if dry_run:
            print("  would merge")
            continue
        p = run(["gh", "pr", "merge", str(pr), "--rebase", "--delete-branch"])
        if p.returncode == 0:
            print("  merged")
            merged_any = True
        else:
            print(f"::warning::#{pr} did not merge, needs a human: {p.stderr.strip()}")

    # A merge made with GITHUB_TOKEN starts no workflow, so CI on main is asked for here.
    if merged_any:
        p = run(["gh", "workflow", "run", "ci.yml", "--ref", "main"])
        if p.returncode != 0:
            print(f"::error::could not dispatch ci.yml on main: {p.stderr.strip()}")
            return 1
        print("dispatched ci.yml on main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
