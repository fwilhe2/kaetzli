"""govulncheck on the Go programs a build ships: is a Go advisory's code in the binary?

grype matches a Go module by version: kubelet links golang.org/x/net v0.57.0, so every
x/net advisory below v0.60.0 is reported, whatever x/net package it is in. Go's
vulnerability database also names the vulnerable functions, and govulncheck's binary
mode reads which functions a program contains (its symbol table, which the linker has
already pruned to what is reachable). An advisory none of whose functions is in any of an
SBOM's binaries becomes an OpenVEX `not_affected` statement for that module version in
that SBOM, `vulnerable_code_not_present`; one whose function is in a binary stays open
and is listed with the binaries that have it.

The judgement is per SBOM, because a module version can be linked into a program in one
extension and inert in another (docker/distribution's is in crio, not in podman). It is
deterministic for a given database: one snapshot (vuln.go.dev/vulndb.zip) per scan, its
date in the report, and govulncheck's version pinned by go/tools/go.mod and go.sum.

Standard library advisories (module "stdlib") have no SBOM package for grype to report;
when their code is in a binary they are listed here and nowhere else — the fix is a Go
toolchain bump (builder/toolchains/go.sh).
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from kaetzli import repo

VULNDB = "https://vuln.go.dev/vulndb.zip"
TOOLS = "go/tools"

Key = tuple[str, str, str]  # (advisory, module, version)


def version() -> str:
    """govulncheck's version, as go/tools/go.mod pins it."""
    gomod = (repo.ROOT / TOOLS / "go.mod").read_text()
    m = re.search(r"^\s*golang\.org/x/vuln (v\S+)", gomod, re.M)
    if not m:
        raise RuntimeError(f"{TOOLS}/go.mod does not require golang.org/x/vuln")
    return m.group(1)


def parse_stream(text: str) -> list[dict]:
    """govulncheck -json's output: JSON objects one after another, not a list."""
    decoder = json.JSONDecoder()
    out, i = [], 0
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return out
        obj, i = decoder.raw_decode(text, i)
        out.append(obj)


def _is_go(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            if f.read(4) != b"\x7fELF":
                return False
            f.seek(0)
            return ELFFile(f).get_section_by_name(".go.buildinfo") is not None
    except (OSError, ELFError):
        return False


def go_binaries(root: Path) -> list[Path]:
    """Every Go program under root: an ELF file with Go build info, symlinks not
    followed, in path order."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if not p.is_symlink() and p.is_file() and _is_go(p):
                found.append(p)
    return found


def sboms_of(path: Path) -> list[str]:
    """The SBOMs a staged file ends up in, from the staging layout: extensions/<name>/
    is ext-<name>, rootfs/ is the disk image and the OCI one."""
    parts = path.parts
    for i, part in enumerate(parts[:-1]):
        if part == "extensions" and i + 1 < len(parts) - 1:
            return [f"ext-{parts[i + 1]}"]
        if part == "rootfs":
            return ["erofs", "oci"]
    return []


@dataclass
class Result:
    db: str = ""
    aliases: dict[str, list[str]] = field(default_factory=dict)
    # sbom -> key -> binaries with the module version, and with a vulnerable function
    present: dict[str, dict[Key, set[str]]] = field(default_factory=dict)
    called: dict[str, dict[Key, set[str]]] = field(default_factory=dict)
    fixed: dict[Key, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def not_linked(self, sbom: str) -> dict[Key, list[str]]:
        called = self.called.get(sbom, {})
        return {
            k: sorted(bins)
            for k, bins in sorted(self.present.get(sbom, {}).items())
            if not called.get(k)
        }

    @property
    def linked(self) -> list[tuple[str, str, str, str, list[str]]]:
        """(advisory, module, version, fixed in, binaries) whose code is in a binary."""
        rows: dict[Key, set[str]] = defaultdict(set)
        for per in self.called.values():
            for k, bins in per.items():
                rows[k] |= bins
        return [(*k, self.fixed.get(k, ""), sorted(b)) for k, b in sorted(rows.items())]


def analyse(runs: Iterable[tuple[list[str], str, list[dict]]]) -> Result:
    """(SBOMs, binary, govulncheck's messages) for each binary scanned."""
    r = Result()
    for sboms, binary, msgs in runs:
        for m in msgs:
            if "config" in m:
                r.db = r.db or (m["config"].get("db_last_modified") or "")[:10]
            elif "osv" in m:
                osv = m["osv"]
                r.aliases[osv["id"]] = list(osv.get("aliases") or [])
            elif "finding" in m:
                f = m["finding"]
                frame = (f.get("trace") or [{}])[0]
                k = (f["osv"], frame.get("module", ""), frame.get("version", ""))
                if f.get("fixed_version"):
                    r.fixed[k] = f["fixed_version"]
                for sbom in sboms:
                    r.present.setdefault(sbom, {}).setdefault(k, set()).add(binary)
                    if frame.get("function"):
                        r.called.setdefault(sbom, {}).setdefault(k, set()).add(binary)
    return r


def _purls(doc: dict) -> set[str]:
    return {
        ref["referenceLocator"]
        for pkg in doc.get("packages") or []
        for ref in pkg.get("externalRefs") or []
        if ref.get("referenceType") == "purl"
    }


def statements(result: Result | None, sbom: str, doc: dict) -> list[dict]:
    """OpenVEX `not_affected` for each advisory whose module version is in this SBOM's
    binaries and whose vulnerable functions are in none of them: one statement per name
    the advisory goes by (GO-, CVE-, GHSA-), since grype reports whichever it matched."""
    if result is None:
        return []
    have = _purls(doc)
    out = []
    for (osv, module, ver), bins in result.not_linked(sbom).items():
        purl = f"pkg:golang/{module}@{ver}"
        if purl not in have:
            continue
        note = (
            f"govulncheck {version()} -mode=binary (Go vulndb {result.db}): none of "
            f"{osv}'s vulnerable functions is in {', '.join(bins)}"
        )
        for name in [osv, *result.aliases.get(osv, [])]:
            out.append(
                {
                    "vulnerability": {"name": name},
                    "products": [{"@id": purl}],
                    "status": "not_affected",
                    "justification": "vulnerable_code_not_present",
                    "impact_statement": note,
                }
            )
    return out


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False, **kw)


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


class Unavailable(RuntimeError):
    pass


def build(out: Path, run: Callable[..., subprocess.CompletedProcess] = _run) -> Path:
    """govulncheck from go/tools, which go.sum pins, with the Go on PATH."""
    go = shutil.which("go")
    if not go:
        raise Unavailable("no go on PATH to build govulncheck with")
    exe = out / "govulncheck"
    p = run(
        [go, "build", "-trimpath", "-o", str(exe), "golang.org/x/vuln/cmd/govulncheck"],
        cwd=repo.ROOT / TOOLS,
        env={**os.environ, "GOFLAGS": "-mod=readonly"},
    )
    if p.returncode != 0:
        raise Unavailable(f"could not build govulncheck: {p.stderr.strip()}")
    return exe


def database(out: Path, fetch: Callable[[str], bytes] = _download) -> Path:
    """Go's vulnerability database, one snapshot for the whole scan."""
    try:
        data = fetch(VULNDB)
    except OSError as e:
        raise Unavailable(f"could not download {VULNDB}: {e}") from e
    d = out / "vulndb"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(d)
    return d


def scan(
    root: Path,
    exe: Path,
    db: Path,
    run: Callable[..., subprocess.CompletedProcess] = _run,
) -> Result:
    """govulncheck on every Go program under root (a directory of staging trees)."""
    runs = []
    errors = []
    for binary in go_binaries(root):
        sboms = sboms_of(binary.relative_to(root))
        if not sboms:
            continue
        p = run([str(exe), "-mode=binary", "-db", db.as_uri(), "-json", str(binary)])
        name = str(binary.relative_to(root))
        if p.returncode != 0:
            errors.append(f"{name}: {p.stderr.strip()}")
            continue
        runs.append((sboms, name, parse_stream(p.stdout)))
    result = analyse(runs)
    result.errors = errors
    return result


def run_all(root: Path) -> Result:
    """Build govulncheck, fetch the database, scan root. Unavailable when either fails."""
    with tempfile.TemporaryDirectory(prefix="govulncheck-") as tmp:
        t = Path(tmp)
        exe = os.environ.get("GOVULNCHECK")
        return scan(root, Path(exe) if exe else build(t), database(t))
