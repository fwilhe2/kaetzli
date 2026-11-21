"""test/cve-not-affected.txt: what grype reports that does not affect this image, and why.

Each line is `<package> <vulnerability> <justification> <reason…>`. It becomes an OpenVEX
`not_affected` statement for every SBOM package of that name, whatever its version: the
reasons are about what the image is (a configure switch, a kernel symbol, a fix years
before our version in an NVD entry with no range), not about one release. An entry no
report mentions any more, open or closed, is stale — the scan lists it to be deleted.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from kaetzli.cve import findings

PATH = "test/cve-not-affected.txt"

# OpenVEX 0.2.0's justifications, the only ones grype and other consumers understand.
JUSTIFICATIONS = (
    "component_not_present",
    "vulnerable_code_not_present",
    "vulnerable_code_not_in_execute_path",
    "vulnerable_code_cannot_be_controlled_by_adversary",
    "inline_mitigations_already_exist",
)

_ID = re.compile(r"^(CVE-\d{4}-\d+|GHSA(-[0-9a-z]{4}){3}|GO-\d{4}-\d+)$")


@dataclass(frozen=True)
class Entry:
    package: str
    vulnerability: str
    justification: str
    reason: str
    line: int
    source: str = PATH


def parse(text: str, source: str = PATH) -> list[Entry]:
    entries: list[Entry] = []
    seen: dict[tuple[str, str], int] = {}
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(maxsplit=3)
        where = f"{source}:{n}"
        if len(fields) < 4:
            raise ValueError(
                f"{where}: needs <package> <vulnerability> <justification> and a reason"
            )
        package, vuln, justification, reason = fields
        if not _ID.match(vuln):
            raise ValueError(f"{where}: {vuln} is not a vulnerability id (CVE-, GHSA-, GO-)")
        if justification not in JUSTIFICATIONS:
            raise ValueError(
                f"{where}: {justification} is not an OpenVEX justification "
                f"({', '.join(JUSTIFICATIONS)})"
            )
        if (package, vuln) in seen:
            raise ValueError(
                f"{where}: {package} {vuln} is listed twice (line {seen[package, vuln]})"
            )
        seen[package, vuln] = n
        entries.append(Entry(package, vuln, justification, reason, n, source))
    return entries


def load(path: Path, source: str = PATH) -> list[Entry]:
    return parse(Path(path).read_text(), source)


def statements(doc: dict, entries: Iterable[Entry]) -> list[dict]:
    """OpenVEX `not_affected` for each entry and each purl of an SBOM package of its name,
    in the SBOM's package order, then the file's."""
    by_package: dict[str, list[Entry]] = {}
    for e in entries:
        by_package.setdefault(e.package, []).append(e)
    out = []
    for pkg in doc.get("packages") or []:
        mine = by_package.get(pkg.get("name") or "")
        if not mine:
            continue
        purls = [
            r["referenceLocator"]
            for r in pkg.get("externalRefs") or []
            if r.get("referenceType") == "purl"
        ]
        for purl in purls:
            for e in mine:
                out.append(
                    {
                        "vulnerability": {"name": e.vulnerability},
                        "products": [{"@id": purl}],
                        "status": "not_affected",
                        "justification": e.justification,
                        "impact_statement": f"{e.reason} ({e.source}:{e.line})",
                    }
                )
    return out


def _ids(match: dict) -> set[str]:
    ids = {match["vulnerability"]["id"]}
    ids |= {r.get("id") for r in match.get("relatedVulnerabilities") or [] if r.get("id")}
    return ids


def stale(entries: Iterable[Entry], reports: Iterable[findings.Report]) -> list[Entry]:
    """The entries no report matches, open or closed: grype has stopped reporting them."""
    reported: set[tuple[str, str]] = set()
    for r in reports:
        for m in r.matches + r.ignored:
            reported |= {(m["artifact"]["name"], i) for i in _ids(m)}
    return [e for e in entries if (e.package, e.vulnerability) not in reported]
