"""kaetzli.source: the corresponding source of a build, from its commit and its sources
image, pulled from a fake registry and checked against the commit's pins."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from kaetzli import repo, source

REF = "ghcr.example/o/kaetzli/sources:0123456789abcdef"
TARBALL = b"the upstream tarball\n"
PATCH = b"--- a/x\n+++ b/x\n"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def layer(files: dict[str, bytes]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return gzip.compress(raw.getvalue(), mtime=0)


class Registry:
    """The distribution API, answering from a dict; blobs redirect to a CDN that must not
    see the registry's token. Records every request."""

    def __init__(self, blob: bytes, *, index=True, corrupt=False):
        self.calls: list[tuple[str, dict]] = []
        digest = "sha256:" + sha(blob)
        manifest = json.dumps({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                        "digest": digest, "size": len(blob)}],
        }).encode()  # fmt: skip
        mdigest = "sha256:" + sha(manifest)
        idx = json.dumps({
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [{"digest": mdigest, "platform": {"architecture": a}}
                          for a in ("arm64", "amd64")],
        }).encode()  # fmt: skip
        base = "https://ghcr.example/v2/o/kaetzli/sources"
        self.routes = {
            "https://ghcr.example/token?scope=repository:o/kaetzli/sources:pull&service=ghcr.example":
                (200, {}, b'{"token": "T"}'),
            f"{base}/manifests/0123456789abcdef": (200, {}, idx if index else manifest),
            f"{base}/manifests/{mdigest}": (200, {}, manifest),
            f"{base}/blobs/{digest}": (307, {"location": "https://cdn.example/blob"}, b""),
            "https://cdn.example/blob": (200, {}, blob + (b"x" if corrupt else b"")),
        }  # fmt: skip

    def __call__(self, url: str, headers: dict) -> source.Response:
        self.calls.append((url, headers))
        if url.startswith("https://cdn.example/"):
            assert "Authorization" not in headers
        elif "/v2/" in url:
            assert headers.get("Authorization") == "Bearer T"
        status, h, body = self.routes.get(url, (404, {}, b""))
        return source.Response(status, h, io.BytesIO(body))


def test_pull_follows_the_index_and_extracts_the_layer(tmp_path):
    reg = Registry(layer({"sources/a-1.tar.gz": TARBALL}))
    source.pull(REF, tmp_path, reg)
    assert (tmp_path / "sources" / "a-1.tar.gz").read_bytes() == TARBALL
    assert sorted(p.name for p in tmp_path.iterdir()) == ["sources"]  # no blobs left


def test_blobs_are_staged_beside_the_destination_not_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(source.tempfile, "tempdir", "/nonexistent")
    source.pull(REF, tmp_path, Registry(layer({"sources/a-1.tar.gz": TARBALL})))
    assert (tmp_path / "sources" / "a-1.tar.gz").exists()


def test_pull_takes_a_plain_manifest_too(tmp_path):
    reg = Registry(layer({"sources/a-1.tar.gz": TARBALL}), index=False)
    source.pull(REF, tmp_path, reg)
    assert (tmp_path / "sources" / "a-1.tar.gz").exists()


def test_a_blob_that_is_not_its_digest_is_refused_and_nothing_extracted(tmp_path):
    reg = Registry(layer({"sources/a-1.tar.gz": TARBALL}), corrupt=True)
    with pytest.raises(source.SourceError, match="does not match its digest"):
        source.pull(REF, tmp_path, reg)
    assert not (tmp_path / "sources").exists()


def test_a_missing_image_says_which(tmp_path):
    reg = Registry(layer({}))
    reg.routes = {k: v for k, v in reg.routes.items() if "/manifests/0123" not in k}
    with pytest.raises(source.SourceError, match="sources:0123456789abcdef"):
        source.pull(REF, tmp_path, reg)


# --- checking what came down against the commit -----------------------------------------


def tree(tmp_path: Path, *, patches: bool = True) -> Path:
    """A checkout with this repository's tools/lib.sh, one package and a stand-in
    image-tags.sh."""
    t = tmp_path / "tree"
    (t / "tools").mkdir(parents=True)
    shutil.copy(repo.ROOT / "tools" / "lib.sh", t / "tools" / "lib.sh")
    (t / "tools" / "image-tags.sh").write_text(f'#!/bin/bash\necho "{REF}"\n')
    (t / "tools" / "image-tags.sh").chmod(0o755)
    pkg = t / "packages" / "a"
    pkg.mkdir(parents=True)
    (pkg / "env.sh").write_text(
        f'VERSION=1\nPACKAGE="a-$VERSION"\nTARBALL="$PACKAGE.tar.gz"\nSHA256={sha(TARBALL)}\n'
    )
    if patches:
        (pkg / "patches").mkdir()
        (pkg / "patches" / "fix.patch").write_bytes(PATCH)
    local = t / "packages" / "ours"
    local.mkdir()
    (local / "env.sh").write_text("VERSION=1\nLOCAL_SOURCE=1\n")
    return t


def test_pins_are_every_tarball_but_local_sources(tmp_path):
    assert source.pins(tree(tmp_path)) == [source.Pin("a", "a-1.tar.gz", sha(TARBALL))]


def test_the_ref_is_the_commits_own_image_tags(tmp_path):
    assert source.sources_ref(tree(tmp_path)) == REF


def sources_dir(tmp_path: Path, tarball=TARBALL, patch=PATCH) -> Path:
    s = tmp_path / "out" / "sources"
    (s / "patches" / "a").mkdir(parents=True)
    (s / "a-1.tar.gz").write_bytes(tarball)
    if patch is not None:
        (s / "patches" / "a" / "fix.patch").write_bytes(patch)
    return s


def test_verify_passes_when_every_byte_is_the_commits(tmp_path):
    assert source.verify(tree(tmp_path), sources_dir(tmp_path)) == []


def test_verify_names_a_tarball_that_is_not_its_pin(tmp_path):
    problems = source.verify(tree(tmp_path), sources_dir(tmp_path, tarball=b"other"))
    assert problems == ["a: a-1.tar.gz does not match the pinned SHA-256"]


def test_verify_names_a_missing_tarball(tmp_path):
    s = sources_dir(tmp_path)
    (s / "a-1.tar.gz").unlink()
    assert source.verify(tree(tmp_path), s) == ["a: a-1.tar.gz is not in the sources image"]


def test_verify_names_a_patch_that_differs_from_the_commits(tmp_path):
    problems = source.verify(tree(tmp_path), sources_dir(tmp_path, patch=b"changed"))
    assert problems == ["a: patches/a differ from the commit's packages/a/patches"]


def test_verify_names_patches_the_commit_lacks(tmp_path):
    problems = source.verify(tree(tmp_path, patches=False), sources_dir(tmp_path))
    assert problems == ["a: patches/a differ from the commit's packages/a/patches"]


# --- Go modules (GO_MODULES=1): go.sum, from the verified tarball, pins every file ------

GOMOD = Path(__file__).parent / "fixtures" / "go-modules"
ERRORS = "go-modules/g-1/github.com/pkg/errors/@v/v0.9.1"


def test_go_hash_is_gos_h1_for_a_zip_and_a_go_mod():
    sums = dict(
        (f"{mod} {ver}", h) for mod, ver, h in (ln.split() for ln in (GOMOD / "go.sum").open())
    )
    v = GOMOD / "proxy/github.com/pkg/errors/@v"
    assert source.go_hash(v / "v0.9.1.zip") == sums["github.com/pkg/errors v0.9.1"]
    assert source.go_hash(v / "v0.9.1.mod") == sums["github.com/pkg/errors v0.9.1/go.mod"]


def test_fetch_sources_go_hash_agrees_with_go():
    # The bash one that admits each file into downloads/ (tools/fetch-sources.sh).
    script = (repo.ROOT / "tools/fetch-sources.sh").read_text()
    funcs = script[script.index("go_escape()") : script.index("verify()")]
    v = GOMOD / "proxy/github.com/pkg/errors/@v"
    out = subprocess.run(
        ["bash", "-c", f'{funcs}\ngo_hash "$1"; go_hash "$2"; go_escape "$3"', "_",
         v / "v0.9.1.zip", v / "v0.9.1.mod", "github.com/BurntSushi/toml"],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    assert out == [
        "h1:FEBLx1zS214owpjy7qsBeixbURkuhQAwrK5UwLGTwt4=",
        "h1:bwawxfHBFNV+L2hUp1rHADufV3IMtnDRdf1r5NINEl0=",
        "github.com/!burnt!sushi/toml",
    ]


def go_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout with one GO_MODULES package, g, and a sources image that is all its."""
    t = tree(tmp_path, patches=False)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        data = (GOMOD / "go.sum").read_bytes()
        info = tarfile.TarInfo("plugins-1/go.sum")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    tarball = raw.getvalue()
    (t / "packages/g").mkdir()
    (t / "packages/g/env.sh").write_text(
        f'VERSION=1\nPACKAGE="g-$VERSION"\nTARBALL="$PACKAGE.tar.gz"\nSHA256={sha(tarball)}\n'
        "GO_MODULES=1\n"
    )
    s = sources_dir(tmp_path, patch=None)
    shutil.rmtree(s / "patches")
    (s / "g-1.tar.gz").write_bytes(tarball)
    shutil.copytree(GOMOD / "proxy", s / "go-modules/g-1")
    return t, s


def test_verify_passes_go_modules_that_are_go_sums(tmp_path):
    assert source.verify(*go_tree(tmp_path)) == []


def test_verify_names_a_go_module_that_is_not_go_sums(tmp_path):
    t, s = go_tree(tmp_path)
    (s / f"{ERRORS}.zip").write_bytes(b"other")
    assert source.verify(t, s) == [f"g: {ERRORS}.zip does not match go.sum"]


def test_verify_names_a_missing_go_module(tmp_path):
    t, s = go_tree(tmp_path)
    (s / f"{ERRORS}.mod").unlink()
    assert source.verify(t, s) == [f"g: {ERRORS}.mod is not in the sources image"]


def test_verify_names_a_file_go_sum_does_not(tmp_path):
    t, s = go_tree(tmp_path)
    (s / f"{ERRORS}.info").write_text("{}")
    assert source.verify(t, s) == [f"g: {ERRORS}.info is not in go.sum"]


# --- the whole run, against a throwaway git repository ----------------------------------


def git_repo(tmp_path: Path) -> tuple[Path, str]:
    t = tree(tmp_path)
    git = ["git", "-C", str(t), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "c"], check=True)
    subprocess.run([*git, "tag", "20261103.141205-42"], check=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()  # fmt: skip
    return t, head


def test_run_writes_the_commit_the_sources_a_readme_and_checksums(tmp_path, capsys):
    root, head = git_repo(tmp_path)
    reg = Registry(layer({"sources/a-1.tar.gz": TARBALL, "sources/patches/a/fix.patch": PATCH}))
    out = tmp_path / "result"
    assert source.run("20261103.141205-42", out, root, reg) == 0
    d = out / head
    archive = d / f"kaetzli-{head}.tar.gz"
    with tarfile.open(archive) as t:
        assert f"kaetzli-{head}/packages/a/env.sh" in t.getnames()
    assert (d / "sources" / "a-1.tar.gz").read_bytes() == TARBALL
    assert REF in (d / "README.txt").read_text()
    sums = (d / "SHA256SUMS").read_text().splitlines()
    assert f"{sha(TARBALL)}  sources/a-1.tar.gz" in sums
    assert any(line.endswith(f"  kaetzli-{head}.tar.gz") for line in sums)
    assert not any("SHA256SUMS" in line for line in sums)
    assert f"corresponding source of {head}" in capsys.readouterr().out


def test_run_fails_and_keeps_nothing_when_the_image_is_not_the_commits(tmp_path, capsys):
    root, head = git_repo(tmp_path)
    reg = Registry(layer({"sources/a-1.tar.gz": b"other", "sources/patches/a/fix.patch": PATCH}))
    out = tmp_path / "result"
    assert source.run("HEAD", out, root, reg) == 1
    assert "a-1.tar.gz does not match the pinned SHA-256" in capsys.readouterr().err
    assert not (out / head).exists()


def test_run_refuses_a_revision_git_does_not_know(tmp_path, capsys):
    root, _ = git_repo(tmp_path)
    assert source.run("nosuchrev", tmp_path / "o", root, Registry(layer({}))) == 1
    assert "nosuchrev" in capsys.readouterr().err


# --- one file, for a release page --------------------------------------------------------


def test_pack_is_one_reproducible_tar_under_the_commits_name(tmp_path):
    d = tmp_path / "abc"
    (d / "sources").mkdir(parents=True)
    (d / "sources" / "a-1.tar.gz").write_bytes(TARBALL)
    (d / "README.txt").write_text("r")
    source.pack(d, tmp_path / "one.tar")
    (d / "README.txt").touch()  # a newer mtime must not change the bytes
    source.pack(d, tmp_path / "two.tar")
    assert (tmp_path / "one.tar").read_bytes() == (tmp_path / "two.tar").read_bytes()
    with tarfile.open(tmp_path / "one.tar") as t:
        names = t.getnames()
        assert names == sorted(names)
        assert "kaetzli-source-abc/sources/a-1.tar.gz" in names
        assert {(m.uid, m.gid, m.uname, m.mtime) for m in t} == {(0, 0, "", repo.SOURCE_DATE_EPOCH)}


def test_run_with_tar_leaves_only_the_tar(tmp_path):
    root, head = git_repo(tmp_path)
    reg = Registry(layer({"sources/a-1.tar.gz": TARBALL, "sources/patches/a/fix.patch": PATCH}))
    out, tar = tmp_path / "result", tmp_path / "result" / "kaetzli-source.tar"
    assert source.run("HEAD", out, root, reg, tar=tar) == 0
    assert sorted(p.name for p in out.iterdir()) == ["kaetzli-source.tar"]
    with tarfile.open(tar) as t:
        assert f"kaetzli-source-{head}/SHA256SUMS" in t.getnames()
