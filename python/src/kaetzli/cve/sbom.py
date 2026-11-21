"""What the scan reads from and adds to the SPDX documents image/build-rootfs.sh writes."""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime

_FIX = re.compile(r"^(?P<cve>CVE-[0-9]+-[0-9]+) fixed by (?P<patch>.*)$")


def dumps(doc: object) -> str:
    """JSON as jq prints it: two-space indent, unicode as is, a final newline."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def timestamp(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _refs(pkg: dict, kind: str) -> list[dict]:
    return [r for r in pkg.get("externalRefs") or [] if r.get("referenceType") == kind]


def vex(doc: dict, timestamp: str, extra: list[dict] | None = None) -> dict | None:
    """OpenVEX "fixed" for each CVE a package's patches fix (an SPDX `fix` reference,
    image/sbom.py), the product being the package's purl, which is what grype matches a
    statement against; then `extra` (test/cve-not-affected.txt's and govulncheck's
    `not_affected`). None when there is no statement at all."""
    statements = []
    for pkg in doc.get("packages") or []:
        for purl in _refs(pkg, "purl"):
            for fix in _refs(pkg, "fix"):
                m = _FIX.match(fix.get("comment") or "")
                if not m:
                    continue
                statements.append(
                    {
                        "vulnerability": {"name": m["cve"]},
                        "products": [{"@id": purl["referenceLocator"]}],
                        "status": "fixed",
                        "status_notes": f"fixed by packages/{pkg['name']}/patches/{m['patch']}",
                    }
                )
    statements += extra or []
    if not statements:
        return None
    return {
        "@context": "https://openvex.dev/ns/v0.2.0",
        "@id": "https://github.com/fwilhe2/kaetzli/vex",
        "author": "kaetzli build (tools/patch-cves.sh)",
        "timestamp": timestamp,
        "version": 1,
        "statements": statements,
    }


def cpe_map(env_cpes: dict[str, str]) -> dict[str, list[str]]:
    """Each package's CPE= (space-separated, maybe several), packages without one left out."""
    return {pkg: v.split() for pkg, v in env_cpes.items() if v.split()}


def enrich(doc: dict, cpes: dict[str, list[str]]) -> dict:
    """Today's CPEs, with the SBOM's version in them, for each package that has none.

    For SBOMs from before env.sh had CPE= (test/cve-history.py). By the record's own
    name, as image/sbom.py does: a <pkg>-vendor record is another upstream's code and
    gets none.
    """
    doc = copy.deepcopy(doc)
    for pkg in doc.get("packages") or []:
        mine = cpes.get(pkg.get("name"))
        if not mine or _refs(pkg, "cpe23Type"):
            continue
        version = pkg.get("versionInfo") or ""
        added = []
        for cpe in mine:
            parts = cpe.split(":")
            parts[5] = version
            added.append(
                {
                    "referenceCategory": "SECURITY",
                    "referenceType": "cpe23Type",
                    "referenceLocator": ":".join(parts),
                }
            )
        pkg["externalRefs"] = (pkg.get("externalRefs") or []) + added
    return doc
