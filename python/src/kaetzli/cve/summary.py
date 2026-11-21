"""<dir>/cve-summary.txt: one line per SBOM and one for the whole build.

    db <date> grype-<version>
    doc <name> total N critical N high N medium N low N other N fixable N patched N not_affected N
    all total N ...

test/cve-scan.py writes it, test/cve-history.py plots it, and CI keeps it in the
cve-<arch> artifact, so the format outlives any one version of these scripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass(frozen=True)
class Counts:
    """Open CVEs by severity, those with a fix upstream, those our patches close, and those
    VEX says do not affect the image (test/cve-not-affected.txt, govulncheck). A line from
    before not_affected existed parses with 0 there."""

    total: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    other: int = 0
    fixable: int = 0
    patched: int = 0
    not_affected: int = 0

    def line(self) -> str:
        return " ".join(f"{f.name} {getattr(self, f.name)}" for f in fields(self))

    @classmethod
    def parse(cls, line: str) -> Counts | None:
        words = line.split()
        if not words:
            return None
        known = {f.name for f in fields(cls)}
        values = {k: int(v) for k, v in zip(words[::2], words[1::2], strict=False) if k in known}
        return cls(**values)


@dataclass
class Summary:
    db: str = ""
    grype: str = ""
    docs: list[tuple[str, Counts]] = field(default_factory=list)
    all: Counts | None = None

    @property
    def extensions(self) -> int:
        return sum(1 for name, _ in self.docs if name.startswith("ext-"))

    def text(self) -> str:
        lines = [f"db {self.db} {self.grype}"]
        lines += [f"doc {name} {c.line()}" for name, c in self.docs]
        if self.all is not None:
            lines.append(f"all {self.all.line()}")
        return "\n".join(lines) + "\n"

    @classmethod
    def parse(cls, text: str) -> Summary:
        s = cls()
        for line in text.splitlines():
            kind, _, rest = line.partition(" ")
            if kind == "db":
                s.db, _, s.grype = rest.partition(" ")
            elif kind == "doc":
                name, _, counts = rest.partition(" ")
                s.docs.append((name, Counts.parse(counts) or Counts()))
            elif kind == "all":
                s.all = Counts.parse(rest)
        return s

    @classmethod
    def read(cls, path: Path) -> Summary:
        try:
            return cls.parse(path.read_text())
        except FileNotFoundError:
            return cls()
