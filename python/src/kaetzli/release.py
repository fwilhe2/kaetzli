"""Rolling snapshots of main as GitHub releases (docs/proposals/release.md):

    ./tools/release.py assemble ARTIFACTS DIR    # a run's artifacts → the assets
    ./tools/release.py publish DIR --name NAME   # the assets → the snapshot release

A snapshot is a green build of main's head, named `YYYYMMDD.HHMMSS-<run_number>` from the
commit's UTC committer date and ci.yml's run number; ci.yml's `changes` job names it once,
and the image carries the name as IMAGE_VERSION. No fixes are backported and there is
no version to pin: the only fix is a newer snapshot.

`assemble` collects every asset from the run's downloaded artifacts under its release
name (the one optional asset is the CVE workbook; anything else missing fails), makes
each arch's OpenVEX from its disk SBOM, and writes SHA256SUMS. CI attests the assets
between the two steps. `publish`:

1. stops, changing nothing, unless the commit is still main's head and strictly ahead
   of the latest snapshot's, and no release has the name;
2. creates a draft with every asset and the notes, then publishes it as latest, so
   nothing is visible half-uploaded;
3. deletes snapshots at least EXPIRE_DAYS old, and their tags, never the latest.

Asset names carry the architecture and not the snapshot, so
`releases/latest/download/<asset>` is a stable URL. GitHub talks through `gh`, which
holds the auth; tests inject its runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TextIO

from kaetzli import image_diff, repo
from kaetzli.cve import sbom

Runner = Callable[..., subprocess.CompletedProcess]

REPO = "fwilhe2/kaetzli"
ARCHS = ("amd64", "arm64")
EXPIRE_DAYS = 37
_NAME = re.compile(r"^[0-9]{8}\.[0-9]{6}-[0-9]+$")

NOTICE = (
    "> Rolling snapshot. No fixes are backported; the only fix is a newer snapshot. "
    f"Deleted after at least {EXPIRE_DAYS} days."
)


def is_snapshot(tag: str) -> bool:
    return bool(_NAME.match(tag))


class Missing(Exception):
    """A required asset is not among the artifacts."""


@dataclass
class Assets:
    files: list[Path] = field(default_factory=list)  # SHA256SUMS last
    missing_optional: list[str] = field(default_factory=list)
    no_vex: list[str] = field(default_factory=list)  # arches no patch fixes a CVE for


def _copy(src: Path, dst: Path) -> None:
    if not src.is_file() or src.is_symlink():
        raise Missing(f"{src.parent.name}/{src.name} is not among the artifacts")
    shutil.copyfile(src, dst)


def _kernel(tar: Path, dst: Path) -> None:
    """rootfs/boot/bzImage out of the kernel package's tar (Image on arm64, under the
    same name), its member names checked first, as tools/fetch-image.sh does."""
    if not tar.is_file():
        raise Missing(f"{tar.parent.name}/{tar.name} is not among the artifacts")
    with tarfile.open(tar) as t:
        names = t.getnames()
        bad = [n for n in names if n.startswith("/") or ".." in n.split("/")]
        if bad:
            raise Missing(f"{tar.parent.name}/kernel.tar has unsafe member names: {bad}")
        try:
            member = t.getmember("rootfs/boot/bzImage")
        except KeyError:
            raise Missing(f"{tar.parent.name}/kernel.tar has no rootfs/boot/bzImage") from None
        if not member.isfile():
            raise Missing(f"{tar.parent.name}/kernel.tar: rootfs/boot/bzImage is not a file")
        with t.extractfile(member) as f, open(dst, "wb") as out:
            shutil.copyfileobj(f, out)


def assemble(artifacts: Path, out: Path, archs=ARCHS, extensions: set[str] | None = None) -> Assets:
    """Every asset of a snapshot into out/, under its release name, and SHA256SUMS.

    extensions: the system extensions every arch must have (each package's EXTENSION=);
    a configuration extension is required where either arch has one."""
    if extensions is None:
        extensions = {v for v in repo.env_get_all("EXTENSION").values() if v}
    out.mkdir(parents=True, exist_ok=True)
    a = Assets()

    confexts = {
        p.stem
        for arch in archs
        for p in (artifacts / f"extensions-{arch}" / "confexts").glob("*.raw")
    }
    for arch in archs:
        r, s = artifacts / f"rootfs.erofs-{arch}", artifacts / f"sbom-{arch}"
        _copy(r / "rootfs.erofs", out / f"rootfs-{arch}.erofs")
        _copy(r / "rootfs.cmdline", out / f"rootfs-{arch}.cmdline")
        _kernel(artifacts / f"package-kernel-{arch}" / "kernel.tar", out / f"kernel-{arch}")
        e = artifacts / f"extensions-{arch}"
        kinds = (("sysext", "extensions", extensions), ("confext", "confexts", confexts))
        for kind, d, names in kinds:
            for name in sorted(names):
                for suffix in ("raw", "dm-verity"):
                    _copy(e / d / f"{name}.{suffix}", out / f"{name}-{arch}.{kind}.{suffix}")
        _copy(s / "sbom-erofs.json", out / f"sbom-{arch}.json")
        _copy(s / "sbom-oci.json", out / f"sbom-oci-{arch}.json")
        for name in sorted(extensions):
            _copy(s / f"sbom-ext-{name}.json", out / f"sbom-ext-{name}-{arch}.json")
        # The OpenVEX cve-scan.py makes, made the same way here from the disk SBOM, so the
        # release does not depend on the scan job, which gates nothing.
        disk = json.loads((s / "sbom-erofs.json").read_text())
        doc = sbom.vex(disk, sbom.timestamp(repo.SOURCE_DATE_EPOCH))
        if doc is None:
            a.no_vex.append(arch)
        else:
            (out / f"vex-{arch}.json").write_text(sbom.dumps(doc))
        ods = artifacts / f"cve-{arch}" / "cve.ods"
        if ods.is_file():
            _copy(ods, out / f"cve-{arch}.ods")
        else:
            a.missing_optional.append(f"cve-{arch}.ods")

    a.files = sorted(p for p in out.iterdir() if p.name != "SHA256SUMS")
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in a.files]
    (out / "SHA256SUMS").write_text("".join(lines))
    a.files.append(out / "SHA256SUMS")
    return a


def _subprocess_run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False, **kw)


class Releases:
    """The repository's releases, through gh."""

    def __init__(self, repo_name: str = REPO, run: Runner | None = None):
        self.repo = repo_name
        self._run = run or _subprocess_run

    def _gh(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        p = self._run(["gh", *args])
        if check and p.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args)}: {p.stderr.strip()}")
        return p

    def api(self, path: str) -> str | None:
        """GET repos/<repo>/<path>, or None for a 404."""
        p = self._gh("api", f"repos/{self.repo}/{path}", check=False)
        if p.returncode != 0:
            if "404" in p.stderr:
                return None
            raise RuntimeError(f"gh api {path}: {p.stderr.strip()}")
        return p.stdout

    def main_head(self) -> str:
        p = self._gh("api", f"repos/{self.repo}/commits/main", "--jq", ".sha")
        return p.stdout.strip()

    def latest(self) -> tuple[str, str] | None:
        out = self.api("releases/latest")
        if out is None:
            return None
        doc = json.loads(out)
        return doc["tag_name"], doc["target_commitish"]

    def compare(self, base: str, head: str) -> str:
        p = self._gh("api", f"repos/{self.repo}/compare/{base}...{head}", "--jq", ".status")
        return p.stdout.strip()

    def exists(self, tag: str) -> bool:
        return self.api(f"releases/tags/{tag}") is not None

    def download(self, tag: str, patterns: list[str], dest: Path) -> bool:
        args = ["release", "download", tag, "-R", self.repo, "-D", str(dest)]
        for p in patterns:
            args += ["-p", p]
        return self._gh(*args, check=False).returncode == 0

    def create_draft(self, tag: str, sha: str, notes: Path, files: list[Path]) -> None:
        self._gh(
            "release", "create", tag, "-R", self.repo, "--draft", "--target", sha,
            "--title", tag, "--notes-file", str(notes), *map(str, files),
        )  # fmt: skip

    def publish(self, tag: str) -> None:
        self._gh("release", "edit", tag, "-R", self.repo, "--draft=false", "--latest")

    def listing(self) -> list[tuple[str, str, bool]]:
        """(tag, created_at, draft) for every release."""
        p = self._gh(
            "api", f"repos/{self.repo}/releases?per_page=100", "--paginate",
            "--jq", '.[] | [.tag_name, .created_at, .draft] | @tsv',
        )  # fmt: skip
        out = []
        for line in p.stdout.splitlines():
            tag, created, draft = line.split("\t")
            out.append((tag, created, draft == "true"))
        return out

    def delete(self, tag: str) -> None:
        self._gh("release", "delete", tag, "-R", self.repo, "--cleanup-tag", "--yes")


def changes(rel: Releases, previous: str | None, out: Path) -> list[str]:
    """The packages that moved since the previous snapshot, from its SBOM assets and
    ours, by test/image-diff.py's rows (amd64: the arches build the same versions)."""
    if previous is None:
        return ["This is the first snapshot."]
    with tempfile.TemporaryDirectory() as tmp:
        old = Path(tmp) / "old"
        if not rel.download(previous, ["sbom-*amd64.json"], old):
            return [f"No SBOMs to compare against in {previous}."]

        def side(d: Path, prefix: str) -> image_diff.Side:
            plain = Path(tmp) / prefix
            plain.mkdir()
            for p in d.glob("sbom-*amd64.json"):
                name = p.name.removesuffix("-amd64.json")
                name = "sbom-erofs" if name == "sbom" else name
                shutil.copyfile(p, plain / f"{name}.json")
            return image_diff.load(plain)

        rows = image_diff.package_rows(side(old, "a"), side(out, "b"))
    if not rows:
        return [f"No package changed since {previous}."]
    return [
        f"Since {previous}:",
        "",
        "| package | before | after | in |",
        "| --- | --- | --- | --- |",
        *(f"| `{k}` | {a} | {b} | {w} |" for k, a, b, w in rows),
    ]


def notes(
    name: str, sha: str, run_url: str, images: list[str], changed: list[str], a: Assets,
    repo_name: str = REPO,
) -> str:  # fmt: skip
    url = f"https://github.com/{repo_name}/releases/latest/download"
    lines = [
        NOTICE,
        "",
        f"- Commit: [`{sha}`](https://github.com/{repo_name}/commit/{sha})",
        f"- CI run: {run_url}",
        *(f"- Built with `{i}`" for i in images),
        "",
        "The sources image holds every pinned tarball and patch, including the source of",
        "the copyleft packages.",
        "",
        "## What changed",
        "",
        *changed,
        "",
        "## Boot it",
        "",
        "```sh",
        "for f in rootfs-amd64.erofs rootfs-amd64.cmdline kernel-amd64; do",
        f"    curl -fLO {url}/$f",
        "done",
        "qemu-system-x86_64 -enable-kvm -cpu host -m 1G -nographic \\",
        "    -kernel kernel-amd64 \\",
        "    -drive file=rootfs-amd64.erofs,format=raw,if=virtio,readonly=on \\",
        '    -append "console=ttyS0 systemd.set_credential=login.noauth:yes \\',
        '        $(cat rootfs-amd64.cmdline)"',
        "```",
        "",
        "Log in as `root` or `user`, without a password: the credential allows it on the "
        "console (`Ctrl-a x` quits qemu).",
        "",
        "Every file is in `SHA256SUMS`, and each carries a signed build provenance:",
        f"`gh attestation verify <file> -R {repo_name}`.",
    ]
    if a.missing_optional:
        lines += ["", f"Not in this snapshot: {', '.join(a.missing_optional)} (the CVE scan "
                  "gates nothing)."]  # fmt: skip
    if a.no_vex:
        lines += ["", f"No VEX for {', '.join(a.no_vex)}: no patch fixes a known CVE."]
    return "\n".join(lines) + "\n"


def expire(rel: Releases, keep: str, now: datetime, log: TextIO) -> None:
    cutoff = now - timedelta(days=EXPIRE_DAYS)
    for tag, created, draft in rel.listing():
        if draft or tag == keep or not is_snapshot(tag):
            continue
        if datetime.fromisoformat(created.replace("Z", "+00:00")) <= cutoff:
            print(f">> expiring {tag} (created {created})", file=log)
            rel.delete(tag)


def publish(
    rel: Releases, directory: Path, *, name: str, sha: str, run_url: str, images: list[str],
    now: datetime, assets: Assets | None = None, out: TextIO | None = None,
) -> int:  # fmt: skip
    """Publish the assembled directory as snapshot `name`, or say why not (exit 0)."""
    log = out or sys.stdout
    head = rel.main_head()
    if head != sha:
        print(f">> not publishing: {sha} is no longer the head of main ({head})", file=log)
        return 0
    latest = rel.latest()
    if latest is not None:
        status = rel.compare(latest[1], sha)
        if status != "ahead":
            print(
                f">> not publishing: {sha} is {status} against {latest[0]} ({latest[1]})",
                file=log,
            )
            return 0
    if rel.exists(name):
        print(f">> not publishing: a release {name} already exists", file=log)
        return 0
    a = assets or _assets_of(directory)
    with tempfile.TemporaryDirectory() as tmp:
        n = Path(tmp) / "notes.md"
        changed = changes(rel, latest[0] if latest else None, directory)
        n.write_text(notes(name, sha, run_url, images, changed, a, rel.repo))
        rel.create_draft(name, sha, n, a.files)
    rel.publish(name)
    print(f">> published {name}: {len(a.files)} assets", file=log)
    expire(rel, name, now, log)
    return 0


def _assets_of(out: Path) -> Assets:
    """An assembled directory's assets, as assemble() left them."""
    files = sorted(p for p in out.iterdir() if p.name != "SHA256SUMS")
    a = Assets(files=[*files, out / "SHA256SUMS"])
    names = {p.name for p in files}
    a.missing_optional = [f"cve-{x}.ods" for x in ARCHS if f"cve-{x}.ods" not in names]
    a.no_vex = [x for x in ARCHS if f"vex-{x}.json" not in names]
    return a


def images() -> list[str]:
    out = []
    for arch in ARCHS:
        p = subprocess.run(
            ["./tools/image-tags.sh", "builder"], cwd=repo.ROOT, capture_output=True,
            text=True, check=True, env={**os.environ, "ARCH": arch},
        )  # fmt: skip
        out.append(p.stdout.strip())
    p = subprocess.run(
        ["./tools/image-tags.sh", "sources"], cwd=repo.ROOT, capture_output=True, text=True,
        check=True,
    )  # fmt: skip
    out.append(p.stdout.strip())
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="tools/release.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("assemble", help="a run's artifacts into a snapshot's assets")
    p.add_argument("artifacts", type=Path, help="where download-artifact left the run's")
    p.add_argument("dir", type=Path, help="an empty directory for the assets")
    p = sub.add_parser("publish", help="publish an assembled directory as the snapshot")
    p.add_argument("dir", type=Path)
    p.add_argument("--name", required=True, help="the snapshot's name, as the image has it")
    args = ap.parse_args(argv)

    if args.cmd == "assemble":
        if args.dir.exists() and any(args.dir.iterdir()):
            print(f"error: {args.dir} is not empty", file=sys.stderr)
            return 2
        try:
            a = assemble(args.artifacts.resolve(), args.dir)
        except Missing as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f">> {len(a.files)} assets in {args.dir}")
        return 0

    if not is_snapshot(args.name):
        print(f"error: {args.name!r} is not a snapshot name (YYYYMMDD.HHMMSS-N)", file=sys.stderr)
        return 2
    sha = (
        os.environ.get("GITHUB_SHA")
        or subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo.ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo_name = os.environ.get("GITHUB_REPOSITORY", REPO)
    run_url = f"{server}/{repo_name}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    return publish(
        Releases(repo_name), args.dir.resolve(), name=args.name, sha=sha,
        run_url=run_url, images=images(), now=datetime.now(UTC),
    )  # fmt: skip


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
