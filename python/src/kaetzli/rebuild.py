"""Rebuild a release from its commit, independently, and compare it with what was published:

    tools/rebuild-release.py NAME [--dir DIR] [--no-build]

A snapshot is a tag on the commit it was built from. This checks that commit out into a
clean worktree (DIR/<NAME>, default output/rebuild/), builds everything there the way CI
did — tools/build-image.sh: glibc, the kernel, every package against the glibc-only
sysroot, then assembly with the snapshot's name as KAETZLI_VERSION and the commit as
KAETZLI_BUILD — and compares every asset of this machine's architecture, by SHA-256, with
the release's SHA256SUMS. Release names come from kaetzli.release.collect, the code that
named them, over the worktree's output/ laid out as CI's artifacts.

"same" for every rebuilt asset is the claim CLAUDE.md makes, checked by someone who is not
CI: same commit, same bytes, any machine. What a build does not make is listed, not
judged: the CVE workbook (a scan, dated), the source tar and the launchers, and the other
architecture's assets. Hours, the first time; --no-build compares a worktree built before.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kaetzli import release, repo

# Release assets no build makes: a scan, and what the release job adds for both arches.
NOT_BUILD_OUTPUT = re.compile(r"^(cve-.*\.ods|kaetzli-source\.tar|kaetzli-linux-.*)$")
SUMS_LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9._+-]+)$")


@dataclass(frozen=True)
class Row:
    name: str
    status: str  # same, DIFFERENT, not rebuilt, not in the release


def parse_sums(text: str) -> dict[str, str]:
    """SHA256SUMS as {asset: sha256}; a line that is not a plain asset name is refused."""
    out = {}
    for line in text.splitlines():
        m = SUMS_LINE.match(line)
        if not m:
            raise ValueError(f"not a SHA256SUMS line: {line!r}")
        out[m[2]] = m[1]
    return out


def kernel_tar(worktree: Path) -> bytes:
    """The kernel package's tar as CI's artifact holds it, as far as collect reads it."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        t.add(worktree / "staging/kernel/rootfs/boot/bzImage", arcname="rootfs/boot/bzImage")
    return buf.getvalue()


def _link(src: Path, dest: Path) -> None:
    if not src.is_file():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)


def _artifacts(worktree: Path, arch: str, dest: Path) -> None:
    """The worktree's output/ as the CI artifacts collect reads (ci.yml's upload steps)."""
    out = worktree / "output"
    for f in ("rootfs.erofs", "rootfs.cmdline"):
        _link(out / f, dest / f"rootfs.erofs-{arch}" / f)
    if (worktree / "staging/kernel/rootfs/boot/bzImage").is_file():
        k = dest / f"package-kernel-{arch}" / "kernel.tar"
        k.parent.mkdir(parents=True)
        k.write_bytes(kernel_tar(worktree))
    for sub in ("extensions", "confexts"):
        for f in sorted((out / sub).glob("*")):
            _link(f, dest / f"extensions-{arch}" / sub / f.name)
    for f in sorted(out.glob("sbom-*.json")):
        _link(f, dest / f"sbom-{arch}" / f.name)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def compare(worktree: Path, arch: str, extensions: list[str], sums: dict[str, str]) -> list[Row]:
    """Every asset of this arch, rebuilt or published, and how the two compare."""
    others = [a for a in release.ARCHES if a != arch]
    with tempfile.TemporaryDirectory(dir=worktree) as tmp:
        arts, out = Path(tmp) / "artifacts", Path(tmp) / "assets"
        _artifacts(worktree, arch, arts)
        got = release.collect(arts, out, arch, extensions)
        mine = {a.name: _sha256(a.path) for a in got.assets}
    rows = []
    for name in sorted(set(mine) | set(sums)):
        if any(f"-{o}" in name for o in others):
            continue
        if name not in mine:
            rows.append(Row(name, "not rebuilt"))
        elif name not in sums:
            rows.append(Row(name, "not in the release"))
        else:
            rows.append(Row(name, "same" if mine[name] == sums[name] else "DIFFERENT"))
    return rows


def verdict(rows: list[Row]) -> bool:
    """Whether the build reproduced the release: every asset a build makes is the same."""
    judged = [r for r in rows if not NOT_BUILD_OUTPUT.match(r.name)]
    return bool(judged) and all(r.status == "same" for r in judged)


# --- the run ----------------------------------------------------------------------------

Build = Callable[[Path, dict], int]


def _build(worktree: Path, env: dict) -> int:
    return subprocess.run(
        ["./tools/build-image.sh"], cwd=worktree, env={**os.environ, **env}
    ).returncode


def _worktree(path: Path, sha: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo.ROOT), "worktree", "add", "--detach", str(path), sha],
                   check=True)  # fmt: skip


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def run(
    name: str,
    arch: str,
    workdir: Path,
    *,
    sha: str,
    extensions: list[str],
    build: Build = _build,
    worktree: Callable[[Path, str], None] = _worktree,
    fetch: Callable[[str], bytes] = _fetch,
    repo_name: str = "fwilhe2/kaetzli",
    rebuild: bool = True,
) -> int:
    wt = workdir / name
    if not wt.exists():
        print(f">> {name}: a clean worktree of {sha} in {wt}")
        worktree(wt, sha)
    if rebuild:
        print(f">> {name}: building everything for {arch}, as CI did (hours, the first time)")
        if build(wt, {"KAETZLI_VERSION": name, "KAETZLI_BUILD": sha}) != 0:
            print(f"error: the build failed; the worktree is {wt}", file=sys.stderr)
            return 1
    sums = parse_sums(
        fetch(f"https://github.com/{repo_name}/releases/download/{name}/SHA256SUMS").decode()
    )
    rows = compare(wt, arch, extensions, sums)
    width = max(len(r.name) for r in rows)
    for r in rows:
        print(f"  {r.name:<{width}}  {r.status}")
    if verdict(rows):
        same = sum(r.status == "same" for r in rows)
        print(f">> {name} reproduced: {same} assets byte-identical to the release ({arch})")
        return 0
    print(f">> {name} NOT reproduced on {arch}: the assets above that are not `same` differ")
    return 1


def _resolve(rev: str) -> str | None:
    def parse() -> str | None:
        r = subprocess.run(["git", "-C", str(repo.ROOT), "rev-parse", "--verify", "--quiet",
                            f"{rev}^{{commit}}"], capture_output=True, text=True)  # fmt: skip
        return r.stdout.strip() or None

    if sha := parse():
        return sha
    subprocess.run(["git", "-C", str(repo.ROOT), "fetch", "--quiet", "--tags", "origin"],
                   capture_output=True)  # fmt: skip
    return parse()


def _extensions(worktree: Path) -> list[str]:
    out = subprocess.run(
        ["bash", "-c",
         'source tools/lib.sh; for p in $(all_packages); do env_get "$p" EXTENSION; done'],
        cwd=worktree, check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return sorted({e for e in out.split() if e})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="tools/rebuild-release.py", description=__doc__.split("\n\n")[0]
    )
    ap.add_argument("name", help="a snapshot release, e.g. 20261107.094545-751")
    ap.add_argument("--dir", type=Path, help="where the worktrees go (default output/rebuild)")
    ap.add_argument("--no-build", action="store_true", help="compare a worktree built before")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "fwilhe2/kaetzli"))
    a = ap.parse_args(argv)
    if not release.NAME.match(a.name):
        print(f"error: {a.name!r} is not a snapshot name (YYYYMMDD.HHMMSS-N)", file=sys.stderr)
        return 2
    workdir = a.dir.resolve() if a.dir else repo.ROOT / "output" / "rebuild"
    repo.enter_root()
    sha = _resolve(a.name)
    if not sha:
        print(f"error: no tag {a.name}: is it a release of {a.repo}?", file=sys.stderr)
        return 1
    wt = workdir / a.name
    if not wt.exists():
        _worktree(wt, sha)
    return run(a.name, repo.normalize_arch(), workdir, sha=sha, extensions=_extensions(wt),
               repo_name=a.repo, rebuild=not a.no_build)  # fmt: skip


if __name__ == "__main__":
    sys.exit(main())
