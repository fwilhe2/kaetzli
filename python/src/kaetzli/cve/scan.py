"""Known vulnerabilities in what a build ships, from its SBOMs, as a report.

    ./test/cve-scan.py [dir]         # every sbom-*.json in dir (default output/)
    ENRICH=1 ./test/cve-scan.py dir  # first add today's CPEs to SBOMs that have none

grype matches the SBOMs image/build-rootfs.sh and image/build-extension.sh write: C
packages by the CPE in their env.sh, Go modules and Rust crates by purl. It writes

  <dir>/cve/<sbom>.json     grype's matches, for whoever wants the detail
  <dir>/cve-summary.txt     one line per SBOM and one for the whole build, the series
                            test/cve-history.py plots

and a text job summary. It never fails a build: the count of open CVEs is something to
watch, not to gate on, and a missing scanner or an unreachable database ends in a note.

The count moves for two reasons, and only one of them is ours: a version bump closes
advisories, and the world opens new ones against what has not changed. The database's
build date is recorded with each scan so the two can be told apart; test/cve-history.py
rescans old builds with one database to take the second away.

ENRICH is for SBOMs from before env.sh had CPE= (test/cve-history.py): each package gets
the CPE its env.sh has today, with the SBOM's version in it.

Not affected is not open either. test/cve-not-affected.txt lists what grype matches but
the image is not affected by — an NVD entry with no version range for a bug fixed years
ago, a distribution's own patch, code a configure switch leaves out — each with its
reason (kaetzli.cve.not_affected); every line becomes a VEX `not_affected` statement for
the SBOM packages of that name, counted in its own column. A line no report mentions any
more is listed as stale.

Patched is not open. A package whose packages/<pkg>/patches fix a CVE says so in its SBOM
entry, as an SPDX `fix` reference (tools/patch-cves.sh, image/sbom.py); each SBOM's
references become an OpenVEX document (<dir>/cve/<sbom>.vex.json) that grype applies, so
those matches move from `matches` to `ignoredMatches` with a vex rule. Every count here is
what is open; the patched ones are counted beside it and listed by name.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from kaetzli import repo
from kaetzli.cve import findings, not_affected, sbom
from kaetzli.cve.grype import VERSION, Grype, GrypeUnavailable, binary
from kaetzli.cve.summary import Counts, Summary
from kaetzli.report import Note, Report, run


def scan(
    directory: Path,
    grype: Grype,
    report: Report,
    arch: str,
    cpes: dict[str, list[str]] | None = None,
    unaffected: list[not_affected.Entry] | None = None,
) -> Summary:
    sboms = sorted(directory.glob("sbom-*.json"))
    if not sboms:
        raise Note(f"no SBOMs in {directory}/, so nothing to scan", report)
    out = directory / "cve"
    out.mkdir(parents=True, exist_ok=True)
    if not grype.db_update():
        raise Note("could not update grype's vulnerability database", report)

    summary = Summary(db=grype.db_built(), grype=f"grype-{VERSION}")
    reports: dict[str, findings.Report] = {}
    for path in sboms:
        name = path.stem.removeprefix("sbom-")
        doc = json.loads(path.read_text())
        scanned = path
        if cpes is not None:
            doc = sbom.enrich(doc, cpes)
            scanned = out / f"{name}.sbom.json"
            scanned.write_text(sbom.dumps(doc))
        vex_path = out / f"{name}.vex.json"
        extra = not_affected.statements(doc, unaffected or [])
        vex = sbom.vex(doc, sbom.timestamp(repo.SOURCE_DATE_EPOCH), extra)
        if vex:
            vex_path.write_text(sbom.dumps(vex))
        else:
            vex_path.unlink(missing_ok=True)
        result = grype.scan(scanned, vex_path if vex else None)
        report_path = out / f"{name}.json"
        if result is None:
            print(f"grype failed on {path}", file=sys.stderr)
            report_path.unlink(missing_ok=True)
            continue
        report_path.write_text(result)
        reports[name] = findings.load(report_path)
        summary.docs.append((name, findings.counts(reports[name])))

    # The whole build: the disk image and every extension, each CVE and package once. The
    # OCI image is the disk image's userspace minus a little, so it is not added again.
    whole = [r for name, r in sorted(reports.items()) if name != "oci"]
    top: list[tuple[str, str, int]] = []
    patched: list[tuple[str, str, str, str]] = []
    unaffected_rows: list[tuple[str, str, int]] = []
    if whole:
        merged = findings.merge(whole)
        summary.all = findings.counts(merged)
        top = findings.most_affected(merged)
        patched = findings.patched(merged)
        unaffected_rows = findings.not_affected(merged)
    stale = not_affected.stale(unaffected or [], reports.values()) if reports else []
    (directory / "cve-summary.txt").write_text(summary.text())

    report.lines(render(summary, top, patched, arch, unaffected_rows, stale))
    return summary


def _row(label: str, c: Counts) -> str:
    cells = [
        c.total,
        c.critical,
        c.high,
        c.medium,
        c.low,
        c.other,
        c.fixable,
        c.patched,
        c.not_affected,
    ]
    return f"| {label} | " + " | ".join(str(v) for v in cells) + " |"


def render(summary: Summary, top, patched, arch: str, unaffected=(), stale=()) -> list[str]:
    lines = [
        f"## Known vulnerabilities — {arch}",
        "",
        f"grype {VERSION}, vulnerability database built {summary.db}. Each CVE counted once "
        "per package and version. Reported, never enforced.",
        "",
        "Open CVEs: what this build may be vulnerable to. **with a fix** means upstream has "
        "released one; **patched** counts those our patches already close and **not "
        "affected** those VEX says do not apply here, neither in the other columns.",
        "",
        "| SBOM | open | critical | high | medium | low | other | with a fix | patched "
        "| not affected |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines += [_row(f"`{name}`", c) for name, c in summary.docs]
    if summary.all is not None:
        lines.append(_row("**disk image + extensions**", summary.all))
    if top:
        lines += ["", "Most affected:", "", "| package | version | CVEs |", "| --- | --- | ---: |"]
        lines += [f"| `{pkg}` | {ver} | {n} |" for pkg, ver, n in top]
    if patched:
        lines += [
            "",
            "Patched by packages/*/patches (reported by grype, closed by VEX; not counted as "
            "open):",
            "",
            "| CVE | severity | package | version |",
            "| --- | --- | --- | --- |",
        ]
        lines += [f"| {cve} | {sev} | `{pkg}` | {ver} |" for cve, sev, pkg, ver in patched]
    if unaffected:
        lines += [
            "",
            f"Not affected ({not_affected.PATH}; reported by grype, closed by VEX; not "
            "counted as open):",
            "",
            "| package | version | findings |",
            "| --- | --- | ---: |",
        ]
        lines += [f"| `{pkg}` | {ver} | {n} |" for pkg, ver, n in unaffected]
    if stale:
        lines += [
            "",
            f"Stale in {not_affected.PATH}: no report mentions these any more, so the "
            "lines can go:",
            "",
        ]
        lines += [f"- line {e.line}: `{e.package}` {e.vulnerability}" for e in stale]
    return lines


def main(argv: list[str]) -> int:
    repo.enter_root()
    directory = Path(argv[0] if argv else "output")
    report = Report.from_env()
    try:
        exe = binary(pinned=bool(os.environ.get("GRYPE_PINNED")))
    except GrypeUnavailable as e:
        print(e, file=sys.stderr)
        raise Note(f"could not install grype {VERSION}, so no CVE scan", report) from e
    cpes = None
    if os.environ.get("ENRICH") == "1":
        cpes = sbom.cpe_map(repo.env_get_all("CPE"))
    unaffected = not_affected.load(repo.ROOT / not_affected.PATH)
    scan(
        directory,
        Grype(exe),
        report,
        arch=repo.normalize_arch(),
        cpes=cpes,
        unaffected=unaffected,
    )
    return 0


if __name__ == "__main__":
    sys.exit(run(main, sys.argv[1:]))
