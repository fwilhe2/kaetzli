"""Publish a green build of main as a GitHub release (docs/proposals/release.md):

    ./tools/release.py name                 # this checkout's snapshot name (GITHUB_RUN_NUMBER)
    ./tools/release.py publish --dry-run    # everything up to the first write, then say what
    ./tools/release.py publish              # what ci.yml's release job runs

A snapshot is named `YYYYMMDD.HHMMSS-<run number>`: the commit's committer date in UTC,
then ci.yml's run number, which is unique and keeps its value when a run is retried. The
image is assembled with that name (IMAGE_VERSION in os-release), so `name` is what the
rootfs and reproducible jobs ask, and this module is the one place it is spelled.

`publish` reads the run's artifacts from a directory laid out as `gh run download -D`
and actions/download-artifact leave them, `<dir>/<artifact>/...`, collects the assets
under their release names into --out with SHA256SUMS and the notes, and then, unless
the run should not publish, creates a draft on the commit, uploads everything, and
publishes it as latest: nothing is visible before that last step. Finally it deletes
snapshots at least 37 days old, never the latest.

Whether to publish (`decide`), with every check against main as it is now, since a run
can finish long after the push:

  - The commit must still be what main builds: main's head, or an ancestor of it with
    only documentation after it. A docs-only push skips the build (ci.yml's `changes`),
    so the head of main may never have an image, and requiring the head itself would
    leave the previous build unreleased until the next change to code.
  - It must be newer than the latest snapshot, and something the build reads must have
    changed since: a fresh rebuild of an unchanged main is not a new snapshot.
  - The latest snapshot not being an ancestor of main means main was rewritten. No later
    commit can ever pass the check above, so that fails, for a human, rather than
    skipping quietly forever.

A release of this name that is published already (a retried run) is left as it is. A
draft of this name is a failed attempt's leftover and is replaced: drafts are invisible,
have no tag yet and are not immutable.

--dry-run does every read — the decision, the assets, the image digests, the diff
against the previous snapshot — writes SHA256SUMS and notes.md to --out, says what it
would create, publish and delete, and writes nothing to GitHub. It fails on whatever the
real run would fail on, and it collects even for a run that would be skipped, so a pull
request's CI exercises everything up to the writes. Locally:

    gh run download <run id> -D artifacts
    GITHUB_RUN_NUMBER=<n> ./tools/release.py publish --dry-run --sha <sha> --repo <you>/kaetzli
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kaetzli import image_diff, repo
from kaetzli.cve import sbom as sbom_mod
from kaetzli.report import Report

ARCHES = ("amd64", "arm64")
# The launcher (go/cmd/kaetzli), as tools/build-launcher.sh names it: `uname -s`-`uname -m`,
# so the README's install line works unchanged. The release job builds it into
# <artifacts>/launcher/.
LAUNCHERS = ("kaetzli-linux-x86_64", "kaetzli-linux-aarch64")
# The corresponding source (tools/corresponding-source.py --tar), which the release job
# writes to <artifacts>/source/ before publishing.
SOURCE = "kaetzli-source.tar"
ASSET_MAX = 2 * 2**30  # GitHub's limit for one release asset
NAME = re.compile(r"^[0-9]{8}\.[0-9]{6}-[1-9][0-9]*$")
# What ci.yml's `changes` job skips the build for: docs/** and the root *.md. The two
# have to agree, or a commit is either released twice or never.
DOCS_ONLY = re.compile(r"^(docs/.*|[^/]*\.md)$")
# The compare API lists at most 300 files; at the cap the list is not the whole change.
COMPARE_CAP = 300
KEEP = timedelta(days=37)
NOTES_MAX = 120_000  # GitHub's limit for a release body is 125000 characters
NOTICE = (
    "> Rolling snapshot. No fixes are backported; the only fix is a newer snapshot. "
    "Deleted after at least 37 days."
)
EXT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class ReleaseError(Exception):
    pass


def snapshot_name(committer_epoch: int, run_number: int | str) -> str:
    when = datetime.fromtimestamp(int(committer_epoch), UTC)
    return f"{when:%Y%m%d.%H%M%S}-{int(run_number)}"


# --- whether to publish -----------------------------------------------------------------


@dataclass(frozen=True)
class Compare:
    status: str  # ahead (head descends from base), behind, diverged or identical
    files: list[str] | None  # None: too many to list


def nothing_built_changed(c: Compare) -> bool:
    return c.files is not None and all(DOCS_ONLY.match(f) for f in c.files)


@dataclass(frozen=True)
class Decision:
    action: str  # publish, skip or fail
    reason: str


def decide(
    sha: str, head: str, latest: str | None, compare: Callable[[str, str], Compare]
) -> Decision:
    if sha != head:
        c = compare(sha, head)
        if c.status != "ahead" or not nothing_built_changed(c):
            return Decision("skip", f"main has moved on to {head[:12]}, whose run publishes")
    if latest is None:
        return Decision("publish", "the first snapshot")
    if latest == sha:
        return Decision("skip", f"{sha[:12]} is already the latest snapshot")
    c = compare(latest, sha)
    if c.status == "ahead":
        if nothing_built_changed(c):
            return Decision(
                "skip", f"nothing the build reads changed since the latest snapshot ({latest[:12]})"
            )
        return Decision("publish", f"newer than the latest snapshot ({latest[:12]})")
    if c.status in ("behind", "identical"):
        return Decision("skip", f"older than the latest snapshot ({latest[:12]})")
    return Decision(
        "fail",
        f"the latest snapshot's commit {latest[:12]} is not an ancestor of main: was main "
        "rewritten? No later commit can publish until that snapshot is deleted by hand.",
    )


# --- GitHub, through gh -----------------------------------------------------------------

Runner = Callable[..., subprocess.CompletedProcess]


def _subprocess_run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False, **kw)


class GitHub:
    """The gh calls a release takes. Reads raise ReleaseError when gh fails; writes
    return whether they worked, and the caller decides what a failure means."""

    def __init__(self, repo: str, run: Runner = _subprocess_run):
        self.repo = repo
        self._run = run

    def _get(self, path: str, *extra: str) -> subprocess.CompletedProcess:
        return self._run(["gh", "api", f"repos/{self.repo}/{path}", *extra])

    def _json(self, path: str):
        p = self._get(path)
        if p.returncode != 0:
            raise ReleaseError(f"gh api {path}: {p.stderr.strip()}")
        return json.loads(p.stdout)

    def head(self, branch: str) -> str:
        return self._json(f"git/ref/heads/{branch}")["object"]["sha"]

    def compare(self, base: str, head: str) -> Compare:
        data = self._json(f"compare/{base}...{head}")
        files = [f["filename"] for f in data.get("files") or []]
        return Compare(data["status"], files if len(files) < COMPARE_CAP else None)

    def latest(self) -> tuple[str, str] | None:
        """The latest release's tag and commit, None when there is none."""
        p = self._get("releases/latest")
        if p.returncode != 0:
            if "404" in p.stderr:
                return None
            raise ReleaseError(f"gh api releases/latest: {p.stderr.strip()}")
        tag = json.loads(p.stdout)["tag_name"]
        return tag, self._json(f"commits/{tag}")["sha"]

    def releases(self) -> list[dict]:
        """Every release, drafts included (gh is authenticated)."""
        p = self._run(
            ["gh", "api", "--paginate", f"repos/{self.repo}/releases?per_page=100",
             "--jq", ".[] | @json"]
        )  # fmt: skip
        if p.returncode != 0:
            raise ReleaseError(f"gh api releases: {p.stderr.strip()}")
        return [json.loads(line) for line in p.stdout.splitlines() if line.strip()]

    def download(self, tag: str, pattern: str, dest: Path) -> bool:
        p = self._run(
            ["gh", "release", "download", tag, "-R", self.repo, "-p", pattern, "-D", str(dest)]
        )
        return p.returncode == 0

    def create_draft(self, tag: str, sha: str, notes: Path, files: list[Path]) -> bool:
        p = self._run(
            ["gh", "release", "create", tag, "-R", self.repo, "--draft", "--target", sha,
             "--title", tag, "--notes-file", str(notes), *map(str, files)]
        )  # fmt: skip
        if p.returncode != 0:
            print(p.stderr, file=sys.stderr)
        return p.returncode == 0

    def publish(self, release_id: int) -> bool:
        p = self._run(
            ["gh", "api", "-X", "PATCH", f"repos/{self.repo}/releases/{release_id}",
             "-F", "draft=false", "-f", "make_latest=true"]
        )  # fmt: skip
        if p.returncode != 0:
            print(p.stderr, file=sys.stderr)
        return p.returncode == 0

    def delete_release(self, release_id: int) -> bool:
        p = self._run(["gh", "api", "-X", "DELETE", f"repos/{self.repo}/releases/{release_id}"])
        return p.returncode == 0

    def delete_tag(self, tag: str) -> bool:
        p = self._run(["gh", "api", "-X", "DELETE", f"repos/{self.repo}/git/refs/tags/{tag}"])
        return p.returncode == 0


# --- the assets -------------------------------------------------------------------------


@dataclass(frozen=True)
class Asset:
    name: str
    path: Path


@dataclass
class Collected:
    assets: list[Asset] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    workbook: bool = False
    cve_db: str = ""


def _place(src: Path, dest: Path) -> None:
    dest.unlink(missing_ok=True)
    try:
        os.link(src, dest)  # the images are hundreds of MiB; a copy only across devices
    except OSError:
        shutil.copyfile(src, dest)


def _kernel(tar: Path, dest: Path) -> bool:
    """rootfs/boot/bzImage out of the kernel package's tar (bzImage on both arches;
    packages/kernel/build.sh stages arm64's Image under that name)."""
    try:
        with tarfile.open(tar) as t:
            for m in t.getmembers():
                if m.isfile() and m.name.removeprefix("./") == "rootfs/boot/bzImage":
                    f = t.extractfile(m)
                    if f is None:
                        return False
                    with open(dest, "wb") as out:
                        shutil.copyfileobj(f, out)
                    return True
    except (OSError, tarfile.TarError):
        return False
    return False


def merged_vex(docs: list[dict]) -> dict | None:
    """One OpenVEX document for every SBOM of an arch, each statement once."""
    stamp = sbom_mod.timestamp(repo.SOURCE_DATE_EPOCH)
    merged, seen = None, set()
    for doc in docs:
        v = sbom_mod.vex(doc, stamp)
        if v is None:
            continue
        if merged is None:
            merged = {**v, "statements": []}
        for s in v["statements"]:
            key = (s["vulnerability"]["name"], tuple(p["@id"] for p in s["products"]))
            if key not in seen:
                seen.add(key)
                merged["statements"].append(s)
    return merged


def collect(artifacts: Path, out: Path, arch: str, extensions: list[str]) -> Collected:
    """This arch's assets from the run's artifacts into out/, under their release names.
    Every extension packages/*/env.sh names has to be there; one the run built that no
    env.sh names (a stale tree) is taken as it is."""
    out.mkdir(parents=True, exist_ok=True)
    got = Collected()

    def take(src: Path, name: str, required: bool = True) -> bool:
        if src.is_file() and not src.is_symlink():
            _place(src, out / name)
            got.assets.append(Asset(name, out / name))
            return True
        if required:
            got.problems.append(f"{arch}: no {src.relative_to(artifacts)}, for {name}")
        return False

    rootfs = artifacts / f"rootfs.erofs-{arch}"
    take(rootfs / "rootfs.erofs", f"rootfs-{arch}.erofs")
    take(rootfs / "rootfs.cmdline", f"rootfs-{arch}.cmdline")
    # The kernel and that command line sealed in one EFI binary, unsigned: what a user
    # signs with their own key for Secure Boot (docs/guides/secure-boot.md).
    take(rootfs / "kaetzli.efi", f"uki-{arch}.efi")

    kernel = out / f"kernel-{arch}"
    if _kernel(artifacts / f"package-kernel-{arch}/kernel.tar", kernel):
        got.assets.append(Asset(kernel.name, kernel))
    else:
        got.problems.append(f"{arch}: no rootfs/boot/bzImage in package-kernel-{arch}/kernel.tar")

    ext_dir = artifacts / f"extensions-{arch}"
    built = {p.stem for p in (ext_dir / "extensions").glob("*.raw")}
    for name in sorted(set(extensions) | built):
        if not EXT_NAME.match(name):
            got.problems.append(f"{arch}: refusing the extension name {name!r}")
            continue
        for suffix in ("raw", "dm-verity"):
            take(ext_dir / f"extensions/{name}.{suffix}", f"{name}-{arch}.sysext.{suffix}")
        # Only an extension that stages etc/ has a confext; one that has half of one is
        # broken all the same.
        halves = [ext_dir / f"confexts/{name}.{s}" for s in ("raw", "dm-verity")]
        if any(h.exists() for h in halves):
            for h, suffix in zip(halves, ("raw", "dm-verity"), strict=True):
                take(h, f"{name}-{arch}.confext.{suffix}")

    sboms = artifacts / f"sbom-{arch}"
    docs = []
    pairs = [("sbom-erofs.json", f"sbom-{arch}.json"), ("sbom-oci.json", f"sbom-oci-{arch}.json")]
    pairs += [
        (f"sbom-ext-{e}.json", f"sbom-ext-{e}-{arch}.json")
        for e in sorted(set(extensions) | built)
        if EXT_NAME.match(e)
    ]
    for src, name in pairs:
        if take(sboms / src, name):
            try:
                docs.append(json.loads((sboms / src).read_text()))
            except json.JSONDecodeError as e:
                got.problems.append(f"{arch}: {src} is not JSON: {e}")

    vex = merged_vex(docs)
    if vex is not None:
        (out / f"vex-{arch}.json").write_text(sbom_mod.dumps(vex))
        got.assets.append(Asset(f"vex-{arch}.json", out / f"vex-{arch}.json"))
    else:
        (out / f"vex-{arch}.json").unlink(missing_ok=True)

    cve = artifacts / f"cve-{arch}"
    got.workbook = take(cve / "cve.ods", f"cve-{arch}.ods", required=False)
    summary = cve / "cve-summary.txt"
    if summary.is_file():
        for line in summary.read_text().splitlines():
            if line.startswith("db "):
                got.cve_db = line.split()[1]
    return got


def collect_launcher(artifacts: Path, out: Path) -> Collected:
    """The launcher's binaries from <artifacts>/launcher/ into out/, executable."""
    out.mkdir(parents=True, exist_ok=True)
    got = Collected()
    for name in LAUNCHERS:
        src = artifacts / "launcher" / name
        if src.is_file() and not src.is_symlink():
            _place(src, out / name)
            (out / name).chmod(0o755)
            got.assets.append(Asset(name, out / name))
        else:
            got.problems.append(f"no launcher/{name} (tools/build-launcher.sh)")
    return got


def collect_source(artifacts: Path, out: Path) -> Collected:
    """The corresponding source, one tar, from <artifacts>/source/ into out/."""
    out.mkdir(parents=True, exist_ok=True)
    got = Collected()
    src = artifacts / "source" / SOURCE
    if not src.is_file() or src.is_symlink():
        got.problems.append(f"no source/{SOURCE} (tools/corresponding-source.py --tar)")
    elif (size := src.stat().st_size) > ASSET_MAX:
        got.problems.append(f"{SOURCE} is {size / 2**30:.1f} GiB, over GitHub's 2 GiB per asset")
    else:
        _place(src, out / SOURCE)
        got.assets.append(Asset(SOURCE, out / SOURCE))
    return got


def sha256sums(assets: list[Asset]) -> str:
    lines = []
    for a in sorted(assets, key=lambda a: a.name):
        h = hashlib.sha256()
        with open(a.path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {a.name}\n")
    return "".join(lines)


# --- the build inputs, by digest --------------------------------------------------------

Http = Callable[[str, str, dict], tuple[int, dict, bytes]]


def urllib_http(method: str, url: str, headers: dict) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {}, b""
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {}, b""


MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


def image_digest(ref: str, http: Http = urllib_http) -> str | None:
    """What the registry says the tag points at now, by the distribution API: a token,
    anonymous or from GITHUB_TOKEN, then a HEAD of the manifest. No pull."""
    registry, _, rest = ref.partition("/")
    path, _, tag = rest.rpartition(":")
    auth = {}
    if token := os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
        auth["Authorization"] = "Basic " + base64.b64encode(f"x:{token}".encode()).decode()
    status, _, body = http(
        "GET", f"https://{registry}/token?scope=repository:{path}:pull&service={registry}", auth
    )
    if status != 200:
        return None
    try:
        bearer = json.loads(body)["token"]
    except (ValueError, KeyError):
        return None
    status, headers, _ = http(
        "HEAD",
        f"https://{registry}/v2/{path}/manifests/{tag}",
        {"Authorization": f"Bearer {bearer}", "Accept": MANIFEST_TYPES},
    )
    digest = headers.get("docker-content-digest", "") if status == 200 else ""
    return digest if digest.startswith("sha256:") else None


def build_images(http: Http = urllib_http) -> list[tuple[str, str, str | None]]:
    """The builder per arch and the sources image, as tools/image-tags.sh names them for
    this checkout, each with its digest."""
    out = []
    for label, kind, arch in [
        *((f"builder ({a})", "builder", a) for a in ARCHES),
        ("sources", "sources", ARCHES[0]),
    ]:
        ref = subprocess.run(
            ["tools/image-tags.sh", kind],
            cwd=repo.ROOT,
            env={**os.environ, "ARCH": arch},
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        out.append((label, ref, image_digest(ref, http)))
    return out


# --- the notes --------------------------------------------------------------------------


def _boot(repo_name: str, arch: str) -> list[str]:
    url = f"https://github.com/{repo_name}/releases/latest/download"
    if arch == "amd64":
        qemu, console, machine = "qemu-system-x86_64", "ttyS0", "-enable-kvm -cpu host"
    else:
        qemu, console, machine = (
            "qemu-system-aarch64",
            "ttyAMA0",
            "-machine virt,accel=kvm -cpu host",
        )
    return [
        f"On {arch}:",
        "",
        "```sh",
        *(
            f"curl -fLO {url}/{f}"
            for f in (
                f"rootfs-{arch}.erofs",
                f"rootfs-{arch}.cmdline",
                f"kernel-{arch}",
                "SHA256SUMS",
            )
        ),
        "sha256sum -c --ignore-missing SHA256SUMS",
        f"{qemu} {machine} -m 2048 -smp 2 -nographic -no-reboot \\",
        f"    -kernel kernel-{arch} \\",
        f"    -drive id=root,file=rootfs-{arch}.erofs,format=raw,if=none,readonly=on \\",
        "    -device virtio-blk-pci,drive=root -nic user,model=virtio-net-pci \\",
        f'    -append "$(cat rootfs-{arch}.cmdline) console={console} '
        'init=/usr/lib/systemd/systemd systemd.set_credential=login.noauth:yes"',
        "```",
    ]


def _launcher(repo_name: str) -> list[str]:
    url = f"https://github.com/{repo_name}/releases/latest/download"
    return [
        "Or with the launcher, which fetches and verifies the image itself, keeps `/var` "
        "and logs in over ssh (Linux, with qemu installed):",
        "",
        "```sh",
        f"curl -fLo ~/.local/bin/kaetzli {url}/kaetzli-$(uname -s | tr A-Z a-z)-$(uname -m)",
        "chmod +x ~/.local/bin/kaetzli",
        "kaetzli start --ext podman && kaetzli ssh",
        "```",
    ]


def notes(
    name: str,
    sha: str,
    repo: str,
    run_url: str,
    images: list[tuple[str, str, str | None]],
    previous: str | None,
    changes: list[str],
    missing_workbooks: list[str],
    cve_db: dict[str, str],
) -> str:
    lines = [NOTICE, "", "## Provenance", ""]
    lines.append(f"- Commit: [`{sha}`](https://github.com/{repo}/commit/{sha})")
    if run_url:
        lines.append(f"- CI run: {run_url}")
    lines.append("- Built with:")
    for label, ref, digest in images:
        at = f"`{ref.rpartition(':')[0]}@{digest}`" if digest else "digest unavailable"
        lines.append(f"  - {label}: {at} (tag `{ref.rpartition(':')[2]}`)")
    lines += [
        "",
        "The sources image holds every pinned tarball and patch the build used, which is "
        "the corresponding source for the copyleft packages in these images. "
        f"`{SOURCE}` is that source with the repository at this commit, verified against "
        f"its pins, in one file; `tools/corresponding-source.py {name}` makes it again.",
        "",
        "## License",
        "",
        "The images are free software. Each package in them comes under its own license, "
        "whose text ships in `/usr/share/licenses/<package>/`; the SBOMs (also in the "
        "image, `/usr/share/kaetzli/sbom.json`) list every package with its version, "
        f"license and source. This repository's own files are under its [LICENSE](https://github.com/{repo}/blob/{sha}/LICENSE).",
        "",
        "These images come with ABSOLUTELY NO WARRANTY, to the extent permitted by applicable law.",
        "",
        "## Vulnerabilities",
        "",
        "The SBOMs and `vex-<arch>.json` (the CVEs our patches fix, when there are any) let "
        "any scanner rescan with a current database.",
        "",
    ]
    for arch, db in sorted(cve_db.items()):
        lines.append(f"- `cve-{arch}.ods`: scanned with grype's database built {db}.")
    for arch in missing_workbooks:
        lines.append(f"- `cve-{arch}.ods` is missing: this build's CVE scan made no workbook.")
    lines += ["", f"## Changes since {previous}" if previous else "## Changes", ""]
    lines += changes or ["The first snapshot."]
    lines += ["", "## Boot it", ""]
    for arch in ARCHES:
        lines += [*_boot(repo, arch), ""]
    lines += [*_launcher(repo), ""]
    lines += [
        "`uki-<arch>.efi` is the kernel and `rootfs-<arch>.cmdline` (the root hash) in one "
        "unified kernel image, **unsigned**: the project holds no signing key. Verify it, "
        "sign it with your own key and enroll that key in the firmware, and the VMM can no "
        "longer swap the root hash (`tools/sign-uki.sh --key K.pem --cert C.pem`, amd64 for "
        "now; "
        f"[docs/guides/secure-boot.md](https://github.com/{repo}/blob/{sha}/docs/guides/secure-boot.md)).",
        "",
    ]
    lines.append(
        "The extensions attach as described in "
        f"[docs/guides/sysext.md](https://github.com/{repo}/blob/{sha}/docs/guides/sysext.md)."
    )
    text = "\n".join(lines) + "\n"
    if len(text) > NOTES_MAX:
        text = text[:NOTES_MAX] + "\n\n… cut: the release body is limited to 125000 characters.\n"
    return text


def _previous_side(gh: GitHub, tag: str, arch: str, tmp: Path) -> image_diff.Side | None:
    """The previous snapshot's SBOMs under the names image_diff reads."""
    raw = tmp / f"raw-{arch}"
    if not gh.download(tag, f"sbom-*{arch}.json", raw):
        return None
    side = tmp / f"side-{arch}"
    side.mkdir(parents=True, exist_ok=True)
    for p in raw.glob("*.json"):
        stem = p.name.removesuffix(f"-{arch}.json")
        target = {"sbom": "sbom-erofs", "sbom-oci": "sbom-oci"}.get(stem, stem)
        shutil.copyfile(p, side / f"{target}.json")
    return image_diff.load(side)


def _sizes(prev: dict | None, assets: list[Asset]) -> list[str]:
    old = {a["name"]: a["size"] for a in (prev or {}).get("assets") or []}
    rows = []
    for a in sorted(assets, key=lambda a: a.name):
        if not a.name.endswith((".erofs", ".raw")) and not a.name.startswith("kernel-"):
            continue
        new = a.path.stat().st_size
        before = old.get(a.name)
        change = "new" if before is None else f"{(new - before) / 2**20:+.2f}"
        rows.append(f"| `{a.name}` | {new / 2**20:.2f} | {change} |")
    if not rows:
        return []
    return ["### Asset sizes", "", "| asset | MiB | change |", "| --- | ---: | ---: |", *rows]


def changes(
    gh: GitHub, previous: str, prev_release: dict | None, artifacts: Path, assets: list[Asset]
) -> list[str]:
    lines: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for arch in ARCHES:
            old = _previous_side(gh, previous, arch, Path(tmp))
            if old is None:
                lines += [f"Could not download {previous}'s SBOMs for {arch}.", ""]
                continue
            d = image_diff.Diff(old, image_diff.load(artifacts / f"sbom-{arch}"))
            report = image_diff.report_of(d, arch, previous)
            # one level down: the notes have their own sections
            lines += [("#" + x) if x.startswith("#") else x for x in report] + [""]
    return lines + _sizes(prev_release, assets)


# --- expiry -----------------------------------------------------------------------------


def expired(releases: list[dict], now: datetime, latest_tag: str | None) -> list[dict]:
    """Published snapshots (by the name's shape: nothing else is ours to delete) at least
    KEEP old, counted from publication, never the latest."""
    out = []
    for r in releases:
        tag = r.get("tag_name") or ""
        if r.get("draft") or not NAME.match(tag) or tag == latest_tag:
            continue
        published = r.get("published_at")
        if not published:
            continue
        when = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        if now - when >= KEEP:
            out.append(r)
    return out


# --- the run ----------------------------------------------------------------------------


@dataclass
class Options:
    repo: str
    sha: str
    name: str
    run_url: str
    artifacts: Path
    out: Path
    extensions: list[str]
    dry_run: bool
    now: datetime
    branch: str = "main"


def publish(
    o: Options,
    gh: GitHub,
    images: Callable[[], list[tuple[str, str, str | None]]] = build_images,
    report: Report | None = None,
) -> int:
    rep = report or Report.from_env()
    say = rep.out
    mode = " (dry run)" if o.dry_run else ""
    say(f"## Release {o.name}{mode}")
    say("")
    problems: list[str] = []

    try:
        releases = gh.releases()
        mine = [r for r in releases if r.get("tag_name") == o.name]
        published = next((r for r in mine if not r.get("draft")), None)
        drafts = [r for r in mine if r.get("draft")]
        latest = gh.latest()
        if published:
            decision = Decision("skip", f"{o.name} is published already (a retried run)")
        else:
            decision = decide(o.sha, gh.head(o.branch), latest[1] if latest else None, gh.compare)
    except ReleaseError as e:
        say(f"error: {e}")
        return 1
    say(f"- commit `{o.sha}`: **{decision.action}**, {decision.reason}")
    if decision.action == "fail":
        problems.append(decision.reason)

    if decision.action == "skip" and not o.dry_run:
        return _expire(gh, releases, o, latest[0] if latest else None, say)

    # Everything a real run would put in the release, in --dry-run too, so a pull
    # request's CI fails on what main's would.
    shutil.rmtree(o.out, ignore_errors=True)
    assets: list[Asset] = []
    missing_workbooks, cve_db = [], {}
    for arch in ARCHES:
        got = collect(o.artifacts, o.out, arch, o.extensions)
        assets += got.assets
        problems += got.problems
        if not got.workbook:
            missing_workbooks.append(arch)
        if got.cve_db:
            cve_db[arch] = got.cve_db
    for got in (collect_launcher(o.artifacts, o.out), collect_source(o.artifacts, o.out)):
        assets += got.assets
        problems += got.problems
    imgs = images()
    problems += [f"no digest for {ref}" for _, ref, digest in imgs if not digest]

    previous = latest[0] if latest else None
    prev_release = next((r for r in releases if r.get("tag_name") == previous), None)
    diff = changes(gh, previous, prev_release, o.artifacts, assets) if previous else []
    text = notes(o.name, o.sha, o.repo, o.run_url, imgs, previous, diff, missing_workbooks, cve_db)
    (o.out / "notes.md").write_text(text)
    (o.out / "SHA256SUMS").write_text(sha256sums(assets))
    files = [a.path for a in sorted(assets, key=lambda a: a.name)] + [o.out / "SHA256SUMS"]
    total = sum(f.stat().st_size for f in files)
    say(f"- {len(files)} assets, {total / 2**20:.1f} MiB, in `{o.out}`, with `notes.md`")

    if problems:
        for p in problems:
            say(f"- error: {p}")
        return 1

    if o.dry_run:
        for r in drafts:
            say(f"- would delete the draft {r['id']}, a failed attempt's")
        if decision.action == "publish":
            say(
                f"- would create a draft `{o.name}` on `{o.sha[:12]}`, "
                f"uploading {len(files)} assets"
            )
            say("- would publish it and mark it latest")
        for r in expired(releases, o.now, o.name if decision.action == "publish" else previous):
            say(f"- would delete `{r['tag_name']}` (published {r['published_at']}) and its tag")
        return 0

    for r in drafts:
        if not gh.delete_release(r["id"]):
            say(f"- error: could not delete the draft {r['id']}")
            return 1
        say(f"- deleted the draft {r['id']}, a failed attempt's")
    if not gh.create_draft(o.name, o.sha, o.out / "notes.md", files):
        say("- error: creating the draft failed; a retry replaces what it left")
        return 1
    try:
        draft = next(r for r in gh.releases() if r.get("tag_name") == o.name and r.get("draft"))
    except (StopIteration, ReleaseError):
        say("- error: the draft just created is not in the listing")
        return 1
    if not gh.publish(draft["id"]):
        say(f"- error: publishing the draft {draft['id']} failed; a retry replaces it")
        return 1
    say(f"- published https://github.com/{o.repo}/releases/tag/{o.name}")
    try:
        now_latest = gh.latest()
    except ReleaseError:
        now_latest = None
    if not now_latest or now_latest[0] != o.name:
        print(f"::warning::{o.name} is published but GitHub does not report it as latest")
    return _expire(gh, gh.releases(), o, o.name, say)


def _expire(gh: GitHub, releases: list[dict], o: Options, latest: str | None, say) -> int:
    """Deleting is housekeeping: a failure is a warning, and the next run tries again."""
    for r in expired(releases, o.now, latest):
        tag = r["tag_name"]
        if gh.delete_release(r["id"]) and gh.delete_tag(tag):
            say(f"- deleted `{tag}`, published {r['published_at']}")
        else:
            print(f"::warning::could not delete the expired snapshot {tag} or its tag")
    return 0


# --- the command line -------------------------------------------------------------------


def _committer_epoch(rev: str) -> int:
    return int(
        subprocess.run(
            ["git", "show", "-s", "--format=%ct", rev],
            cwd=repo.ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="release.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    run_number = os.environ.get("GITHUB_RUN_NUMBER")

    n = sub.add_parser("name", help="print the snapshot name for a commit")
    n.add_argument("--sha", default=os.environ.get("GITHUB_SHA", "HEAD"))
    n.add_argument("--run-number", default=run_number, required=not run_number)

    p = sub.add_parser("publish", help="publish this run's build as a snapshot")
    p.add_argument("--dry-run", action="store_true", help="read everything, write nothing")
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "fwilhe2/kaetzli"))
    p.add_argument("--sha", default=os.environ.get("GITHUB_SHA"))
    p.add_argument("--run-number", default=run_number, required=not run_number)
    p.add_argument("--branch", default="main")
    p.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    p.add_argument("--out", type=Path, default=Path("output/release"))
    args = ap.parse_args(argv)

    repo.enter_root()
    sha = args.sha or "HEAD"
    sha = subprocess.run(
        ["git", "rev-parse", "--verify", f"{sha}^{{commit}}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    name = snapshot_name(_committer_epoch(sha), args.run_number)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if args.cmd == "publish" and sha != head:
        # tools/image-tags.sh names the images from the files, not from --sha
        print(f"warning: --sha {sha[:12]} is not this checkout's HEAD; the builder and "
              "sources tags in the notes are HEAD's", file=sys.stderr)  # fmt: skip
    if args.cmd == "name":
        print(name)
        return 0

    server, run_id = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_RUN_ID")
    exts = sorted({v for v in repo.env_get_all("EXTENSION").values() if v})
    return publish(
        Options(
            repo=args.repo,
            sha=sha,
            name=name,
            run_url=f"{server}/{args.repo}/actions/runs/{run_id}" if server and run_id else "",
            artifacts=args.artifacts,
            out=args.out,
            extensions=exts,
            dry_run=args.dry_run,
            now=datetime.now(UTC),
            branch=args.branch,
        ),
        GitHub(args.repo),
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
