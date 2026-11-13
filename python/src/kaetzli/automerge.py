"""Merge the green pull requests update-packages.yml opened (tools/auto-merge.py).

Green means the "Package update CI" check passed and no other check failed or is still
running. A pull request labelled `hold` is left alone, and so is one that changes more
than a bump does (outside_scope): CI being green says the change builds, not that it is
only the bump the workflow made, and anyone who can push to an update/ branch could have
added to it. Each is merged as it is, without
rebasing it onto the current main and building it again: one bump rarely affects
another, CI runs on main after the merges, and a red main does not release. A pull
request that no longer merges cleanly is GitHub's to refuse and a human's to fix.

When is the workflow's (.github/workflows/auto-merge.yml, and ci.yml's update-check job),
not this module's.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable

CI_CHECK = "Package update CI"
HOLD_LABEL = "hold"
PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}
# ci.yml's job that turns the run's result into CI_CHECK and then starts this, so it is
# still running when it is looked at; CI_CHECK already says what it would.
REPORTER = "update-check"

Runner = Callable[[list[str]], subprocess.CompletedProcess]

# What update-packages.yml's bumps write: a package's env.sh (bump-version.sh) and its
# upstream patch series (sync-patches.sh), for the Debian snapshot the two Containerfiles
# (bump-snapshot.sh), for a toolchain its one pin file (bump-toolchain.sh). Never build.sh,
# our own patches or a signing key (upstream-key.asc and builder/toolchains/*-key.asc are a
# person's decision), and never two packages or toolchains at once.
_BUMP = re.compile(r"packages/([^/]+)/(env\.sh|patches/upstream/.+)")
SNAPSHOT_FILES = {"builder/Containerfile", "image/Containerfile"}
_TOOLCHAIN = re.compile(r"builder/toolchains/[^/]+\.sh")
# `gh pr view --json files` lists at most this many; a list that long may not be all.
GH_FILES_CAP = 100


def outside_scope(files: list[str]) -> list[str]:
    """The files a bump would not have changed: empty for one that is only a bump."""
    if not files:
        return ["(no files)"]
    if len(files) >= GH_FILES_CAP:
        return [f"({GH_FILES_CAP} files or more: gh lists no more)"]
    if SNAPSHOT_FILES & set(files):
        return [f for f in files if f not in SNAPSHOT_FILES]
    if _TOOLCHAIN.fullmatch(files[0]):
        return files[1:]
    out, pkg = [], None
    for f in files:
        m = _BUMP.fullmatch(f)
        if m and pkg in (None, m[1]):
            pkg = m[1]
        else:
            out.append(f)
    return out


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def checks_state(rollup: list[dict]) -> str:
    """green, pending, failed or untested, from a pull request's statusCheckRollup:
    check runs (status, conclusion) and commit statuses (state) alike."""
    checks = []
    for c in rollup:
        if c.get("name") == REPORTER:
            continue
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
        view = _gh_json(run, ["gh", "pr", "view", str(pr), "--json", "statusCheckRollup,files"])
        state = checks_state(view["statusCheckRollup"])
        print(f"#{pr}: {state}", flush=True)
        if state != "green":
            continue
        if outside := outside_scope([f["path"] for f in view.get("files") or []]):
            what = ", ".join(outside)
            print(f"::warning::#{pr}: changes more than a bump, needs a human: {what}")
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
