"""How long the image takes to boot, and which way that is going.

test/systemd.py records each boot it makes: systemd-analyze's phases (kernel, userspace,
and firmware or loader where there are any), the total, and the ten slowest units, with
the accelerator qemu ran on, into output/boot-time.txt beside its console log. CI uploads
that as boot-time-<arch>.

    ./test/boot-time-history.py [amd64|arm64]
    RUNS=20 ./test/boot-time-history.py

puts this boot beside the last builds on main, as test/size-history.py does for size:
a sparkline per phase, the units that moved, run by run. Only builds on the same
accelerator are compared: a boot under TCG is an order of magnitude slower and says
nothing about one under KVM. One boot per build is one sample, so a single step is noise
and a trend is news. This never fails a build.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from kaetzli import repo
from kaetzli.gh import Gh
from kaetzli.report import Note, Report, run, sparkline
from kaetzli.size.history import head

RECORD = "output/boot-time.txt"

# systemd's format_timespan units, longest first where one is a prefix of another.
_UNITS = {
    "y": 31557600.0,
    "month": 2629800.0,
    "w": 604800.0,
    "d": 86400.0,
    "h": 3600.0,
    "min": 60.0,
    "ms": 0.001,
    "us": 0.000001,
    "s": 1.0,
}
_TOKEN = re.compile(r"([0-9]+(?:\.[0-9]+)?)(month|min|ms|us|y|w|d|h|s)")
# Blame and moved-unit rows smaller than this are not news.
NEWS = 0.05


def seconds(text: str) -> float:
    """A systemd timespan ("1min 2.345s", "412ms") in seconds."""
    text = text.strip()
    pos, total = 0, 0.0
    for m in _TOKEN.finditer(text):
        if text[pos : m.start()].strip():
            break
        total += float(m.group(1)) * _UNITS[m.group(2)]
        pos = m.end()
    if pos == 0 or text[pos:].strip():
        raise ValueError(f"not a systemd timespan: {text!r}")
    return total


def parse_time(line: str) -> tuple[dict[str, float], float] | None:
    """`systemd-analyze time`'s first line as ({phase: seconds}, total), or None when
    the boot has not finished."""
    m = re.search(r"Startup finished in (.*) = (.*)$", line.strip())
    if not m:
        return None
    phases = {}
    for part in m.group(1).split(" + "):
        p = re.fullmatch(r"(.*) \(([a-z]+)\)", part.strip())
        if not p:
            return None
        phases[p.group(2)] = seconds(p.group(1))
    return phases, seconds(m.group(2))


def parse_blame(lines: list[str]) -> list[tuple[str, float]]:
    """`systemd-analyze blame`'s rows as (unit, seconds): the unit is the last word."""
    out = []
    for line in lines:
        parts = line.strip().rsplit(" ", 1)
        if len(parts) != 2:
            continue
        try:
            out.append((parts[1], seconds(parts[0])))
        except ValueError:
            continue
    return out


@dataclass
class BootTime:
    accel: str  # kvm or tcg
    phases: dict[str, float]
    total: float
    blame: list[tuple[str, float]] = field(default_factory=list)


def render_record(b: BootTime) -> str:
    lines = ["# kaetzli boot time, seconds; written by test/systemd.py", f"accel {b.accel}"]
    lines += [f"phase {name} {s:.3f}" for name, s in b.phases.items()]
    lines.append(f"total {b.total:.3f}")
    lines += [f"blame {s:.3f} {unit}" for unit, s in b.blame]
    return "\n".join(lines) + "\n"


def write(b: BootTime, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_record(b))


def read(path: Path) -> BootTime:
    b = BootTime(accel="", phases={}, total=0.0)
    for line in path.read_text().splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        if parts[0] == "accel":
            b.accel = parts[1]
        elif parts[0] == "phase":
            b.phases[parts[1]] = float(parts[2])
        elif parts[0] == "total":
            b.total = float(parts[1])
        elif parts[0] == "blame":
            b.blame.append((parts[2], float(parts[1])))
    return b


@dataclass
class Build:
    id: str
    sha: str
    created: str
    time: BootTime


def collect(gh: Gh, arch: str, runs: int, workdir: Path, branch: str, skip: str) -> list[Build]:
    """The last `runs` green builds on the branch with a record, oldest first."""
    builds = []
    for r in gh.successful_runs("ci.yml", branch, limit=runs * 2):
        if len(builds) >= runs:
            break
        if r.id == skip:
            continue
        d = workdir / r.id
        if not gh.download(r.id, f"boot-time-{arch}", d) or not (d / "boot-time.txt").is_file():
            continue
        builds.append(Build(r.id, r.sha, r.created[:16], read(d / "boot-time.txt")))
    builds.reverse()
    return builds


def _s(x: float) -> str:
    return f"{x:.2f} s"


def _d(x: float) -> str:
    return f"{x:+.2f} s"


def _phase(t: BootTime, name: str) -> float:
    return t.total if name == "total" else t.phases.get(name, 0.0)


def render(builds: list[Build], now: BootTime, branch: str, here: str) -> list[str]:
    same = [b for b in builds if b.time.accel == now.accel]
    other = len(builds) - len(same)
    lines = [
        f"| phase | last {len(same)} builds on `{branch}` → now | {branch} | this build | Δ |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    rows = [*now.phases, "total"]
    for name in rows:
        series = [_phase(b.time, name) for b in same]
        base = series[-1] if series else 0.0
        cur = _phase(now, name)
        lines.append(
            f"| {name} | `{sparkline([*series, cur])}` | {_s(base)} | **{_s(cur)}** "
            f"| {_d(cur - base)} |"
        )
    lines += [
        "",
        f"`systemd-analyze time`, one boot under {now.accel}: a single step is noise, a "
        "trend is news.",
    ]
    if other:
        accels = ", ".join(sorted({b.time.accel for b in builds} - {now.accel}))
        lines.append(f"{other} build{'s' if other > 1 else ''} under {accels} left out.")
    lines.append("")

    if same:
        base = dict(same[-1].time.blame)
        moved = [
            (unit, s, s - base.get(unit, 0.0))
            for unit, s in now.blame
            if abs(s - base.get(unit, 0.0)) >= NEWS
        ]
        if moved:
            lines += [
                f"<details><summary>Slowest units that moved against `{branch}`</summary>",
                "",
                "| unit | now | Δ |",
                "| --- | ---: | ---: |",
                *(f"| `{u}` | {_s(s)} | {_d(d)} |" for u, s, d in moved),
                "",
                "</details>",
                "",
            ]

    lines += [
        "<details><summary>Run by run</summary>",
        "",
        "| date | commit | accel | total |",
        "| --- | --- | --- | ---: |",
        *(
            f"| {b.created.replace('T', ' ', 1)} | `{b.sha[:7]}` | {b.time.accel} "
            f"| {_s(b.time.total)} |"
            for b in builds
        ),
        f"| **this build** | `{here[:7]}` | {now.accel} | **{_s(now.total)}** |",
        "",
        "</details>",
    ]
    return lines


def history(
    gh: Gh,
    arch: str,
    runs: int,
    now: BootTime,
    rep: Report,
    here: str,
    branch: str = "main",
    skip_run: str = "",
) -> None:
    rep.lines([f"## Boot time — {arch}", ""])
    problem = gh.problem()
    with tempfile.TemporaryDirectory() as workdir:
        builds = [] if problem else collect(gh, arch, runs, Path(workdir), branch, skip_run)
    if not builds:
        rep.lines(["| phase | this build |", "| --- | ---: |"])
        rep.lines(f"| {name} | {_s(s)} |" for name, s in now.phases.items())
        rep.lines([f"| total | **{_s(now.total)}** |", ""])
        if problem:
            raise Note(f"{problem}, so there is no history to compare against.", rep)
        raise Note(f"no boot-time artifacts found on `{branch}` yet.", rep)
    rep.lines(render(builds, now, branch, here))


def main(argv: list[str]) -> int:
    repo.enter_root()
    try:
        arch = repo.normalize_arch((argv[0] if argv else None) or os.environ.get("ARCH"))
    except repo.UnsupportedArch:
        print("usage: boot-time-history.py [amd64|arm64]", file=sys.stderr)
        return 2
    if not Path(RECORD).is_file():
        print(f"no boot time in {RECORD} — test/systemd.py writes it", file=sys.stderr)
        return 0
    history(
        Gh(), arch, int(os.environ.get("RUNS", "12")), read(Path(RECORD)), Report.from_env(),
        here=head(),
        branch=os.environ.get("BASE_BRANCH", "main"),
        skip_run=os.environ.get("GITHUB_RUN_ID", ""),
    )  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(run(main, sys.argv[1:]))
