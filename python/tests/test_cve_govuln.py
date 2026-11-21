import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kaetzli import repo
from kaetzli.cve import govuln


def stream(*msgs: dict) -> str:
    # govulncheck -json writes one indented object after another, not a list.
    return "".join(json.dumps(m, indent=2) + "\n" for m in msgs)


CONFIG = {"config": {"db_last_modified": "2026-11-11T02:51:11Z", "scan_level": "symbol"}}
OSV_NET = {"osv": {"id": "GO-2026-6603", "aliases": ["CVE-2026-78659", "GHSA-aaaa-bbbb-cccc"]}}
OSV_CTD = {"osv": {"id": "GO-2026-5064", "aliases": ["CVE-2026-53492"]}}


def finding(osv, module, version, package=None, function=None, fixed="v0.60.0"):
    frame = {"module": module, "version": version}
    if package:
        frame["package"] = package
    if function:
        frame["function"] = function
    return {"finding": {"osv": osv, "fixed_version": fixed, "trace": [frame]}}


KUBELET = stream(
    CONFIG,
    OSV_NET,
    finding("GO-2026-6603", "golang.org/x/net", "v0.57.0"),
    finding("GO-2026-6603", "golang.org/x/net", "v0.57.0", "golang.org/x/net/http2"),
    finding("GO-2026-6603", "golang.org/x/net", "v0.57.0", "golang.org/x/net/http2", "ServeConn"),
)
CRIO = stream(
    CONFIG,
    OSV_CTD,
    OSV_NET,
    # containerd 1.7: the module is in, none of the vulnerable code is.
    finding("GO-2026-5064", "github.com/containerd/containerd", "v1.7.35", fixed=""),
    finding("GO-2026-6603", "golang.org/x/net", "v0.57.0"),
)
PODMAN = stream(CONFIG, OSV_NET, finding("GO-2026-6603", "golang.org/x/net", "v0.57.0"))


@pytest.fixture
def result():
    return govuln.analyse(
        [
            (["ext-kubernetes"], "usr/bin/kubelet", govuln.parse_stream(KUBELET)),
            (["ext-kubernetes"], "usr/bin/crio", govuln.parse_stream(CRIO)),
            (["ext-podman"], "usr/bin/podman", govuln.parse_stream(PODMAN)),
        ]
    )


def sbom(*purls):
    return {
        "packages": [
            {"name": p, "externalRefs": [{"referenceType": "purl", "referenceLocator": p}]}
            for p in purls
        ]
    }


NET = "pkg:golang/golang.org/x/net@v0.57.0"
CTD = "pkg:golang/github.com/containerd/containerd@v1.7.35"


class TestParse:
    def test_one_object_after_another(self):
        msgs = govuln.parse_stream(KUBELET)
        assert [next(iter(m)) for m in msgs] == ["config", "osv", "finding", "finding", "finding"]


class TestAnalyse:
    def test_not_linked_where_no_binary_of_that_sbom_has_the_function(self, result):
        assert result.not_linked("ext-kubernetes") == {
            ("GO-2026-5064", "github.com/containerd/containerd", "v1.7.35"): ["usr/bin/crio"]
        }
        # In podman the same module version is in, its vulnerable code is not.
        assert result.not_linked("ext-podman") == {
            ("GO-2026-6603", "golang.org/x/net", "v0.57.0"): ["usr/bin/podman"]
        }

    def test_linked_is_by_binary_with_the_function(self, result):
        assert result.linked == [
            ("GO-2026-6603", "golang.org/x/net", "v0.57.0", "v0.60.0", ["usr/bin/kubelet"])
        ]

    def test_the_database_date(self, result):
        assert result.db == "2026-11-11"


class TestStatements:
    def test_one_per_name_the_advisory_has(self, result):
        s = govuln.statements(result, "ext-kubernetes", sbom(NET, CTD))
        assert [(x["vulnerability"]["name"], x["products"][0]["@id"]) for x in s] == [
            ("GO-2026-5064", CTD),
            ("CVE-2026-53492", CTD),
        ]
        assert s[0]["status"] == "not_affected"
        assert s[0]["justification"] == "vulnerable_code_not_present"
        assert "usr/bin/crio" in s[0]["impact_statement"]
        assert "govulncheck" in s[0]["impact_statement"]

    def test_linked_code_gets_none(self, result):
        # kubelet has http2.ServeConn: x/net stays open in the kubernetes extension.
        s = govuln.statements(result, "ext-kubernetes", sbom(NET))
        assert s == []

    def test_only_for_a_purl_the_sbom_has(self, result):
        assert govuln.statements(result, "ext-podman", sbom(CTD)) == []

    def test_no_result_no_statements(self):
        assert govuln.statements(None, "ext-podman", sbom(NET)) == []


class TestWhichSbom:
    @pytest.mark.parametrize(
        ("path", "sboms"),
        [
            ("cri-o/extensions/kubernetes/usr/bin/crio", ["ext-kubernetes"]),
            ("extensions/podman/usr/bin/podman", ["ext-podman"]),
            ("foo/rootfs/usr/bin/foo", ["erofs", "oci"]),
            ("foo/elsewhere/bar", []),
        ],
    )
    def test_from_the_staging_layout(self, path, sboms):
        assert govuln.sboms_of(Path(path)) == sboms


class TestGoBinaries:
    def test_only_elf_files_with_go_build_info(self, tmp_path):
        tree = tmp_path / "pkg" / "extensions" / "x" / "usr" / "bin"
        tree.mkdir(parents=True)
        (tree / "script").write_text("#!/bin/sh\n")
        shutil.copy(os.path.realpath(sys.executable), tree / "c")  # an ELF, not Go
        go = shutil.which("go")
        if not go:
            pytest.skip("no Go toolchain to build a Go binary with")
        src = tmp_path / "src"
        src.mkdir()
        (src / "go.mod").write_text("module hello\n\ngo 1.21\n")
        (src / "main.go").write_text("package main\n\nfunc main() {}\n")
        subprocess.run(
            [go, "build", "-o", tree / "hello-go", "."],
            cwd=src,
            check=True,
            env={**os.environ, "GOTOOLCHAIN": "local", "GOFLAGS": ""},
        )
        (tree / "link").symlink_to("hello-go")
        assert govuln.go_binaries(tmp_path) == [tree / "hello-go"]


def test_the_pinned_version_is_go_tools_gomod():
    gomod = (repo.ROOT / "go" / "tools" / "go.mod").read_text()
    assert f"golang.org/x/vuln {govuln.version()} " in gomod


class TestUnavailable:
    def test_no_go_on_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(govuln.shutil, "which", lambda _name: None)
        with pytest.raises(govuln.Unavailable, match="no go on PATH"):
            govuln.build(tmp_path)

    def test_a_failed_build_says_why(self, tmp_path):
        def fails(argv, **_kw):
            return subprocess.CompletedProcess(argv, 1, "", "go: missing go.sum entry")

        if not shutil.which("go"):
            pytest.skip("build() looks for go before running it")
        with pytest.raises(govuln.Unavailable, match=r"missing go\.sum entry"):
            govuln.build(tmp_path, run=fails)

    def test_an_unreachable_database(self, tmp_path):
        def offline(_url):
            raise OSError("network is unreachable")

        with pytest.raises(govuln.Unavailable, match=r"vulndb\.zip"):
            govuln.database(tmp_path, fetch=offline)

    def test_a_binary_govulncheck_fails_on_is_named_not_fatal(self, tmp_path):
        tree = tmp_path / "p" / "extensions" / "x" / "usr" / "bin"
        tree.mkdir(parents=True)

        def run(argv, **_kw):
            return subprocess.CompletedProcess(argv, 1, "", "unrecognized binary format")

        go = shutil.which("go")
        if not go:
            pytest.skip("no Go toolchain to build a Go binary with")
        src = tmp_path / "src"
        src.mkdir()
        (src / "go.mod").write_text("module hello\n\ngo 1.21\n")
        (src / "main.go").write_text("package main\n\nfunc main() {}\n")
        subprocess.run(
            [go, "build", "-o", tree / "hello", "."],
            cwd=src,
            check=True,
            env={**os.environ, "GOTOOLCHAIN": "local", "GOFLAGS": ""},
        )
        r = govuln.scan(tmp_path, Path("govulncheck"), tmp_path, run=run)
        assert r.errors == ["p/extensions/x/usr/bin/hello: unrecognized binary format"]
