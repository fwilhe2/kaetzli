"""Known vulnerabilities over time, and what the package updates did to them:

    ./test/cve-history.py              # this architecture, the last 15 green builds on main
    ./test/cve-history.py arm64        # name one
    RUNS=40 ./test/cve-history.py      # look further back

Two series, because the count moves for two reasons:

  same database   every build's SBOMs (the sbom-<arch> artifact) rescanned now, with one
                  vulnerability database (test/cve-scan.py, with ENRICH for SBOMs from
                  before env.sh had CPE=), and today's test/cve-not-affected.txt.
                  Advisories published since are counted against old and new builds
                  alike, so this moves only when the image changed: a bump that closes
                  some is a step down.
  at build time   what the cve-scan job found on the day (the cve-<arch> artifact): the
                  exposure of the image as published, newly disclosed advisories included.
                  Only as far back as that job has run.

Each step is put down to the commits between the two builds: "update" when all of them
are the updater's ("Update <pkg> to <version>"), "other" when none is, "mixed" when both.
The totals at the end are the same-database steps summed by that label, which is the
answer to "how much do the updates help".

Builds from before the extensions had SBOMs are the disk image alone; the series says so
when it changes shape. History reaches as far back as artifact retention. Like
test/size-history.py this reports and never fails.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kaetzli import repo
from kaetzli.cve import not_affected, sbom
from kaetzli.cve.grype import VERSION, Grype, GrypeUnavailable, binary
from kaetzli.cve.scan import scan
from kaetzli.cve.summary import Summary
from kaetzli.gh import Gh
from kaetzli.report import Note, Report, run, sparkline

_UPDATE = re.compile(r"^Update .* to ")


def label(subjects: list[str] | None) -> str:
    """The commits between two builds: the updater's, someone else's, or both."""
    if subjects is None:
        return "?"
    if not subjects:
        return "—"
    ups = sum(bool(_UPDATE.match(s)) for s in subjects)
    if ups and ups == len(subjects):
        return "update"
    return "other" if ups == 0 else "mixed"


def git_subjects(older: str, newer: str) -> list[str] | None:
    p = subprocess.run(
        ["git", "log", "--format=%s", f"{older}..{newer}"],
        cwd=repo.ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    return p.stdout.splitlines() if p.returncode == 0 else None


def label_between(older: str, newer: str) -> str:
    return label(git_subjects(older, newer))


@dataclass
class Build:
    run: str
    sha: str
    date: str
    now: Summary  # rescanned today
    then: Summary  # the build's own scan


def _shape(s: Summary) -> str:
    return "disk" if s.extensions == 0 else f"disk + {s.extensions} ext"


def render(builds: list[Build], arch: str, label_of: Callable[[str, str], str]) -> list[str]:
    db = builds[0].now.db if builds else ""
    lines = [
        f"## Known vulnerabilities over time — {arch}",
        "",
        f"Same database: every build rescanned with the vulnerability database of {db or 'today'}."
        " At build time: what that build's own scan found.",
        "",
        "| date | commit | change | same database | step | with a fix | at build time | SBOMs |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    sums = {"update": 0, "other": 0, "mixed": 0}
    series: list[int] = []
    then_series: list[int] = []
    prev: Build | None = None
    for b in builds:
        if b.now.all is None:
            continue
        total, then_total = b.now.all.total, b.then.all.total if b.then.all else None
        shape = _shape(b.now)
        step, change = "", "—"
        if prev is not None:
            change = label_of(prev.sha, b.sha)
            d = total - prev.now.all.total
            step = f"+{d}" if d > 0 else str(d)
            # A step where the extensions first appear is the SBOM growing, not the image.
            if shape == _shape(prev.now):
                sums[change] = sums.get(change, 0) + d
            else:
                step = f"({step}, new SBOMs)"
        lines.append(
            f"| {b.date} | `{b.sha[:7]}` | {change} | {total} | {step} | {b.now.all.fixable} "
            f"| {'—' if then_total is None else then_total} | {shape} |"
        )
        series.append(total)
        if then_total is not None:
            then_series.append(then_total)
        prev = b
    lines += ["", "| series | trend |", "| --- | --- |", f"| same database | {sparkline(series)} |"]
    if then_series:
        lines.append(f"| at build time | {sparkline(then_series)} |")
    lines += [
        "",
        "Steps summed by what landed between builds (same database, so only the image's own "
        f"changes): updates {sums['update']}, other commits {sums['other']}, "
        f"mixed {sums['mixed']}.",
    ]
    return lines


def collect(gh: Gh, arch: str, runs: int, workdir: Path, workflow: str, branch: str) -> list[Build]:
    """The last `runs` green builds that still have their SBOMs, oldest first. A run that
    failed before `rootfs` has none, and artifacts expire, so twice as many are asked."""
    builds = []
    for r in gh.successful_runs(workflow, branch, limit=runs * 2):
        if len(builds) >= runs:
            break
        d = workdir / r.id
        if not gh.download(r.id, f"sbom-{arch}", d):
            continue
        gh.download(r.id, f"cve-{arch}", d / "then")
        builds.append(
            Build(r.id, r.sha, r.date, Summary(), Summary.read(d / "then" / "cve-summary.txt"))
        )
    builds.reverse()
    return builds


def rescan(
    builds: list[Build],
    workdir: Path,
    grype: Grype,
    cpes: dict[str, list[str]],
    unaffected: list[not_affected.Entry] | None = None,
) -> None:
    """Every build with one database: today's, today's CPEs for SBOMs without, and today's
    not-affected list (its reasons hold for any version)."""
    for b in builds:
        d = workdir / b.run
        # A build that cannot be rescanned has no summary and is left out of the table.
        with contextlib.suppress(Note):
            scan(d, grype, Report.quiet(), arch="", cpes=cpes, unaffected=unaffected)
        b.now = Summary.read(d / "cve-summary.txt")


def history(
    gh: Gh,
    grype: Grype | None,
    arch: str,
    runs: int,
    workdir: Path,
    cpes: dict[str, list[str]],
    rep: Report,
    workflow: str = "ci.yml",
    branch: str = "main",
    label_of: Callable[[str, str], str] = label_between,
    unaffected: list[not_affected.Entry] | None = None,
) -> None:
    builds = collect(gh, arch, runs, workdir, workflow, branch)
    if not builds:
        raise Note(
            f"no sbom-{arch} artifacts on `{branch}` — they expire, and a branch that never "
            "ran has none",
            rep,
        )
    assert grype is not None
    rescan(builds, workdir, grype, cpes, unaffected)
    rep.lines(render(builds, arch, label_of))


def main(argv: list[str]) -> int:
    repo.enter_root()
    try:
        arch = repo.normalize_arch((argv[0] if argv else None) or os.environ.get("ARCH"))
    except repo.UnsupportedArch:
        print("usage: cve-history.py [amd64|arm64]", file=sys.stderr)
        return 2
    rep = Report.from_env()
    gh = Gh()
    if problem := gh.problem():
        raise Note(f"{problem}, so no history", rep)
    try:
        grype = Grype(binary(pinned=bool(os.environ.get("GRYPE_PINNED"))))
    except GrypeUnavailable as e:
        raise Note(f"could not install grype {VERSION}, so no history", rep) from e
    cpes = sbom.cpe_map(repo.env_get_all("CPE"))
    with tempfile.TemporaryDirectory() as workdir:
        history(
            gh, grype, arch, int(os.environ.get("RUNS", "15")), Path(workdir), cpes, rep,
            workflow=os.environ.get("WORKFLOW", "ci.yml"),
            branch=os.environ.get("BASE_BRANCH", "main"),
            unaffected=not_affected.load(repo.ROOT / not_affected.PATH),
        )  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(run(main, sys.argv[1:]))
