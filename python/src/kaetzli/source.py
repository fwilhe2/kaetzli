"""The complete corresponding source of a build, in one directory, for whoever asks.

    tools/corresponding-source.py [REV] [--out DIR] [--tar FILE]

REV is the commit an image was built from: a release name (each snapshot is a tag), a
commit (an image's BUILD_ID in /etc/os-release, its OCI label
org.opencontainers.image.revision), or anything else git resolves. Default HEAD.

Into DIR/<commit>/ (default output/source/) it writes:

  kaetzli-<commit>.tar.gz   the repository at that commit: every build script, the kernel
                            configuration, the image assembly, the sources this repository
                            holds itself (packages/*/src) and every patch
  sources/                  that commit's sources image: every upstream tarball the build
                            compiled, and patches/<pkg>/ as they were applied
  README.txt                what these are and how to rebuild from them
  SHA256SUMS                every file above

The sources image is the one tools/image-tags.sh names *at that commit* (its tag is a
hash of the commit's pins), pulled over the registry's HTTP API, so nothing but git and
python is needed. Nothing is kept unless every tarball matches the commit's pinned
SHA-256 and every patch directory the commit's own.

--tar FILE packs that directory into one reproducible tar instead (pack()): what the
release job uploads to every snapshot as kaetzli-source.tar.
"""

from __future__ import annotations

import argparse
import base64
import filecmp
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from kaetzli import repo


class SourceError(Exception):
    pass


# --- the registry -----------------------------------------------------------------------


@dataclass
class Response:
    status: int
    headers: dict
    body: BinaryIO


Http = Callable[[str, dict], Response]

MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def urllib_http(url: str, headers: dict) -> Response:
    """GET, redirects not followed: pull() follows a blob's itself, without the token."""
    try:
        r = _opener.open(urllib.request.Request(url, headers=headers), timeout=120)
        return Response(r.status, {k.lower(): v for k, v in r.headers.items()}, r)
    except urllib.error.HTTPError as e:
        return Response(e.code, {k.lower(): v for k, v in e.headers.items()}, io.BytesIO())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError(f"{url}: {e}") from e


def _json(http: Http, url: str, headers: dict, what: str) -> dict:
    r = http(url, headers)
    if r.status != 200:
        raise SourceError(f"{what}: HTTP {r.status} from {url}")
    return json.loads(r.body.read())


def _blob(http: Http, url: str, headers: dict, digest: str, dest: BinaryIO) -> None:
    r = http(url, headers)
    if r.status in (301, 302, 303, 307, 308):
        r = http(r.headers["location"], {})  # a signed URL: the token is not for it
    if r.status != 200:
        raise SourceError(f"layer {digest}: HTTP {r.status}")
    h = hashlib.sha256()
    while chunk := r.body.read(1 << 20):
        h.update(chunk)
        dest.write(chunk)
    if "sha256:" + h.hexdigest() != digest:
        raise SourceError(f"layer {digest} does not match its digest")


def pull(ref: str, dest: Path, http: Http = urllib_http) -> None:
    """Every layer of the image at `ref`, each verified, extracted into `dest`. The
    sources image is data, so any platform of a manifest list is the same bytes."""
    registry, _, rest = ref.partition("/")
    path, _, tag = rest.rpartition(":")
    auth = {}
    if token := os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
        auth["Authorization"] = "Basic " + base64.b64encode(f"x:{token}".encode()).decode()
    token_url = f"https://{registry}/token?scope=repository:{path}:pull&service={registry}"
    bearer = _json(http, token_url, auth, f"{ref}: no registry token").get("token")
    headers = {"Authorization": f"Bearer {bearer}", "Accept": MANIFEST_TYPES}
    base = f"https://{registry}/v2/{path}"
    m = _json(http, f"{base}/manifests/{tag}", headers, f"{ref}: no such image")
    if "manifests" in m:
        m = _json(http, f"{base}/manifests/{m['manifests'][0]['digest']}", headers, ref)
    # Beside dest, not in /tmp: the image is ~450 MB, and /tmp is often a small tmpfs.
    with tempfile.TemporaryDirectory(prefix=".blobs-", dir=dest) as tmp:
        blobs = []
        for i, lay in enumerate(m.get("layers", [])):
            f = Path(tmp) / str(i)
            with f.open("wb") as out:
                _blob(http, f"{base}/blobs/{lay['digest']}", headers, lay["digest"], out)
            blobs.append(f)
        for f in blobs:
            with tarfile.open(f, mode="r:*") as t:
                t.extractall(dest, filter="data")


# --- the commit -------------------------------------------------------------------------


@dataclass(frozen=True)
class Pin:
    pkg: str
    tarball: str
    sha256: str


def _bash(tree: Path, script: str) -> str:
    return subprocess.run(
        ["bash", "-c", f"source tools/lib.sh\n{script}"],
        cwd=tree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def pins(tree: Path) -> list[Pin]:
    """Every tarball the commit's packages pin, as its env.sh files say (read by bash,
    through that commit's own tools/lib.sh)."""
    out = _bash(
        tree,
        """for p in $(all_packages); do
             ( load_env "$p"
               [ -n "${LOCAL_SOURCE:-}" ] && exit 0
               printf '%s\\t%s\\t%s\\n' "$p" "$TARBALL" "$SHA256"
               [ -z "${VENDOR_TARBALL:-}" ] ||
                   printf '%s\\t%s\\t%s\\n' "$p" "$VENDOR_TARBALL" "$VENDOR_SHA256" )
           done""",
    )
    return [Pin(*line.split("\t")) for line in out.splitlines()]


def go_module_packages(tree: Path) -> list[tuple[str, str, str]]:
    """(pkg, PACKAGE, TARBALL) for each GO_MODULES=1 package: its modules are in the
    sources image as go-modules/<PACKAGE>/, laid out as the module proxy serves them."""
    out = _bash(
        tree,
        """for p in $(all_packages); do
             ( load_env "$p"
               [ -z "${GO_MODULES:-}" ] ||
                   printf '%s\\t%s\\t%s\\n' "$p" "$PACKAGE" "$TARBALL" )
           done""",
    )
    return [tuple(line.split("\t")) for line in out.splitlines()]  # type: ignore[misc]


def go_escape(s: str) -> str:
    """The module proxy's case-encoding: an upper-case letter is ! and its lower case."""
    return "".join(f"!{c.lower()}" if c.isupper() else c for c in s)


def go_hash(f: Path) -> str:
    """go.sum's h1: hash (golang.org/x/mod's dirhash.Hash1) of a module zip or go.mod:
    SHA-256 over "<sha256>  <name>" lines, names sorted bytewise."""
    if f.suffix == ".mod":
        files = [("go.mod", f.read_bytes())]
    else:
        try:
            with zipfile.ZipFile(f) as z:
                files = [(i.filename, z.read(i)) for i in z.infolist() if not i.is_dir()]
        except zipfile.BadZipFile:
            return "not a zip"
    summary = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(files, key=lambda x: x[0].encode())
    )
    return "h1:" + base64.b64encode(hashlib.sha256(summary.encode()).digest()).decode()


def _go_sum(tarball: Path) -> str:
    with tarfile.open(tarball) as t:
        for m in t.getmembers():
            if m.isfile() and m.name.count("/") == 1 and m.name.endswith("/go.sum"):
                return t.extractfile(m).read().decode()  # type: ignore[union-attr]
    return ""


def _verify_go_modules(pkg: str, package: str, tarball: str, sources: Path) -> list[str]:
    """go-modules/<package>/ against the go.sum in its (already checked) tarball: every
    file it names, matching, and nothing else."""
    root = sources / "go-modules" / package
    want: dict[str, str] = {}
    for line in _go_sum(sources / tarball).splitlines():
        mod, ver, h = line.split()
        ext = ".mod" if ver.endswith("/go.mod") else ".zip"
        ver = ver.removesuffix("/go.mod")
        want[f"{go_escape(mod)}/@v/{go_escape(ver)}{ext}"] = h
    problems = []
    for rel, h in sorted(want.items()):
        f = root / rel
        if not f.is_file():
            problems.append(f"{pkg}: go-modules/{package}/{rel} is not in the sources image")
        elif go_hash(f) != h:
            problems.append(f"{pkg}: go-modules/{package}/{rel} does not match go.sum")
    if root.is_dir():
        for f in sorted(root.rglob("*")):
            rel = f.relative_to(root).as_posix()
            if f.is_file() and rel not in want:
                problems.append(f"{pkg}: go-modules/{package}/{rel} is not in go.sum")
    return problems


def sources_ref(tree: Path) -> str:
    """The sources image the commit names: its own tools/image-tags.sh."""
    return subprocess.run(
        ["tools/image-tags.sh", "sources"], cwd=tree, check=True, capture_output=True, text=True
    ).stdout.strip()


def _same_tree(a: Path, b: Path) -> bool:
    c = filecmp.dircmp(a, b)
    if c.left_only or c.right_only or c.funny_files:
        return False
    if filecmp.cmpfiles(a, b, c.common_files, shallow=False)[1:] != ([], []):
        return False
    return all(_same_tree(a / d, b / d) for d in c.common_dirs)


def verify(tree: Path, sources: Path) -> list[str]:
    """What in `sources` is not the commit's: tarballs against their pins, patch
    directories against packages/<pkg>/patches."""
    problems = []
    for p in pins(tree):
        f = sources / p.tarball
        if not f.is_file():
            problems.append(f"{p.pkg}: {p.tarball} is not in the sources image")
            continue
        h = hashlib.sha256()
        with f.open("rb") as fh:
            while chunk := fh.read(1 << 20):
                h.update(chunk)
        if h.hexdigest() != p.sha256:
            problems.append(f"{p.pkg}: {p.tarball} does not match the pinned SHA-256")
    for pkg in sorted({p.pkg for p in pins(tree)}):
        ours, theirs = tree / "packages" / pkg / "patches", sources / "patches" / pkg
        if ours.is_dir() != theirs.is_dir() or (ours.is_dir() and not _same_tree(ours, theirs)):
            problems.append(f"{pkg}: patches/{pkg} differ from the commit's packages/{pkg}/patches")
    bad = {p.split(":")[0] for p in problems}
    for pkg, package, tarball in go_module_packages(tree):
        if pkg in bad:
            continue  # its go.sum is in a tarball already found wanting
        problems += _verify_go_modules(pkg, package, tarball, sources)
    return problems


# --- one file ---------------------------------------------------------------------------


def pack(d: Path, tar: Path) -> None:
    """d as one uncompressed tar (what is in it is compressed already), every entry under
    kaetzli-source-<d's name>/, sorted, owned by root and dated SOURCE_DATE_EPOCH, so the
    same source is the same bytes."""
    top = f"kaetzli-source-{d.name}"

    def norm(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mtime = repo.SOURCE_DATE_EPOCH
        info.mode = 0o755 if info.isdir() or info.mode & 0o111 else 0o644
        return info

    tar.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar, "w", format=tarfile.PAX_FORMAT) as t:
        t.add(d, arcname=top, recursive=False, filter=norm)
        for p in sorted(d.rglob("*"), key=lambda p: p.relative_to(d).as_posix()):
            t.add(p, arcname=f"{top}/{p.relative_to(d).as_posix()}", recursive=False,
                  filter=norm)  # fmt: skip


# --- the run ----------------------------------------------------------------------------

README = """\
The complete corresponding source of the Kaetzli images built from commit
{sha}.

  kaetzli-{sha}.tar.gz
      The repository at that commit: the scripts that build every package
      (packages/<pkg>/build.sh), the kernel configuration (packages/kernel/build.sh),
      the image assembly (image/), the sources the repository holds itself
      (packages/<pkg>/src/) and every patch (packages/<pkg>/patches/).

  sources/
      Every upstream tarball the build compiled, from the sources image
      {ref}
      and sources/patches/<pkg>/, the patches as they were applied. Every tarball here
      matches the SHA-256 pinned in packages/<pkg>/env.sh. sources/go-modules/<package>/
      are the Go modules of a package whose tarball has no vendor/ (GO_MODULES=1), each
      matching the go.sum in that tarball.

  SHA256SUMS
      Every file above.

To rebuild: unpack the repository, copy sources/*.tar.* and sources/go-modules/ into its
downloads/ and run
./tools/build-image.sh (README.md says what the host needs). The compilers are Debian's,
from the snapshot builder/Containerfile pins.

Each package's license is in its tarball, and in the image under
/usr/share/licenses/<package>/.
"""


def _resolve(root: Path, rev: str) -> str | None:
    def parse() -> str | None:
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
            capture_output=True,
            text=True,
        )
        return r.stdout.strip() or None

    if sha := parse():
        return sha
    # A release newer than this clone: its tag is on the remote.
    subprocess.run(["git", "-C", str(root), "fetch", "--quiet", "--tags", "origin"],
                   capture_output=True)  # fmt: skip
    return parse()


def _sha256sums(d: Path) -> str:
    lines = []
    for f in sorted(p for p in d.rglob("*") if p.is_file() and p.name != "SHA256SUMS"):
        h = hashlib.sha256()
        with f.open("rb") as fh:
            while chunk := fh.read(1 << 20):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {f.relative_to(d).as_posix()}")
    return "\n".join(lines) + "\n"


def run(
    rev: str, out: Path, root: Path = repo.ROOT, http: Http = urllib_http, tar: Path | None = None
) -> int:
    sha = _resolve(root, rev)
    if not sha:
        print(f"error: git knows no commit {rev!r} (a release name, a commit or a ref)",
              file=sys.stderr)  # fmt: skip
        return 1
    final = out / sha
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{sha[:12]}-", dir=out))
    try:
        archive = work / f"kaetzli-{sha}.tar.gz"
        with archive.open("wb") as f:
            subprocess.run(
                ["git", "-C", str(root), "archive", "--format=tar.gz",
                 f"--prefix=kaetzli-{sha}/", sha],
                stdout=f, check=True,
            )  # fmt: skip
        tree = work / ".tree"
        tree.mkdir()
        with tarfile.open(archive) as t:
            t.extractall(tree, filter="data")
        tree = tree / f"kaetzli-{sha}"
        ref = sources_ref(tree)
        print(f">> {sha}: pulling {ref}")
        pull(ref, work, http)
        if problems := verify(tree, work / "sources"):
            print(f"error: {ref} is not the source of {sha}:", file=sys.stderr)
            for p in problems:
                print(f"       {p}", file=sys.stderr)
            return 1
        shutil.rmtree(work / ".tree")
        (work / "README.txt").write_text(README.format(sha=sha, ref=ref))
        (work / "SHA256SUMS").write_text(_sha256sums(work))
        if final.exists():
            shutil.rmtree(final)
        work.rename(final)
    except (SourceError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if tar:
        pack(final, tar)
        shutil.rmtree(final)
        final = tar
    print(f">> the corresponding source of {sha} is in {final}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="tools/corresponding-source.py", description=__doc__.split("\n\n")[0]
    )
    ap.add_argument("rev", nargs="?", default="HEAD", help="release name, commit or ref")
    ap.add_argument("--out", type=Path, help="default output/source in the checkout")
    ap.add_argument("--tar", type=Path, help="pack it into this one file instead (a release's)")
    a = ap.parse_args(argv)
    out = a.out.resolve() if a.out else repo.ROOT / "output" / "source"
    tar = a.tar.resolve() if a.tar else None
    repo.enter_root()
    return run(a.rev, out, tar=tar)


if __name__ == "__main__":
    sys.exit(main())
