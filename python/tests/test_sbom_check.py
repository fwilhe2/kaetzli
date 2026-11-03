"""test/check-sbom.py on one good SBOM and one broken in each way it checks.

fixtures/sbom/check/<case>/ is an SBOM (the bash image/sbom.sh's, for
fixtures/sbom/tree, then broken) and the manifest beside it; expected/ is what the bash
test/check-sbom.sh printed and returned for it. Two deliberate differences: a list of
names no longer ends in a space, and an unparseable document is described by Python's
json, not jq.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from kaetzli import repo, sbom_check

CHECK = Path(__file__).parent / "fixtures" / "sbom" / "check"
CASES = sorted(p.name for p in CHECK.iterdir())


def rel(p: Path) -> str:
    return str(p.relative_to(repo.ROOT))


@pytest.mark.parametrize("case", CASES)
def test_the_output_is_the_bash_versions(case, capsys, monkeypatch):
    monkeypatch.chdir(repo.ROOT)
    expected = CHECK / case / "expected"
    rc = sbom_check.main([rel(CHECK / case / "sbom-erofs.json")])
    out, err = capsys.readouterr()
    assert (out, err, rc) == (
        (expected / "stdout.txt").read_text(),
        (expected / "stderr.txt").read_text(),
        int((expected / "rc").read_text()),
    )


def test_every_broken_case_fails_and_names_something():
    for case in CASES:
        fails, _ = sbom_check.check(CHECK / case / "sbom-erofs.json")
        assert bool(fails) == (case != "good"), case


def test_a_license_text_is_a_file_under_the_packages_directory(tmp_path):
    doc = json.loads((CHECK / "good" / "sbom-erofs.json").read_text())
    (tmp_path / "sbom-erofs.json").write_text(json.dumps(doc))
    names = ["gawk", "glibc", "fetch", "netavark", "podman", "tzdata"]
    # A directory alone, or a file of another name with the same prefix, is not one.
    lines = [f"usr/share/licenses/{n}/COPYING" for n in names[1:]]
    lines += ["usr/share/licenses/gawk", "usr/share/licenses/gawk/", "usr/share/licenses/gawkx/C"]
    (tmp_path / "rootfs-files-erofs.txt").write_text("\n".join(lines) + "\n")
    fails, _ = sbom_check.check(tmp_path / "sbom-erofs.json")
    assert fails == ["packages shipped without a license text in usr/share/licenses: gawk"]


def test_no_documents_is_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(repo, "enter_root", lambda: os.chdir(tmp_path))
    assert sbom_check.main([]) == 1
    assert "no SBOM in output/" in capsys.readouterr().err


def test_the_defaults_are_both_flavours_in_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(repo, "enter_root", lambda: os.chdir(tmp_path))
    (tmp_path / "output").mkdir()
    for f in ("sbom-erofs.json", "rootfs-files-erofs.txt"):
        (tmp_path / "output" / f).write_bytes((CHECK / "good" / f).read_bytes())
    assert sbom_check.main([]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "output/sbom-erofs.json"


def test_the_entry_point(tmp_path):
    good = rel(CHECK / "good" / "sbom-erofs.json")
    bad = rel(CHECK / "unhashed" / "sbom-erofs.json")
    r = subprocess.run(
        [str(repo.ROOT / "test" / "check-sbom.py"), good, bad],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1
    assert r.stdout.splitlines()[0] == good
    assert r.stderr == "  FAIL: tarball packages with no SHA256: gawk netavark\n"


def test_the_spdx_model_is_checked(tmp_path):
    """spdx-tools' validator, beyond what the checks above re-derive: here a `fix` that is
    no URL and a LicenseRef- with no text, which the bash writer wrote."""
    doc = json.loads((CHECK / "good" / "sbom-erofs.json").read_text())
    glibc = next(p for p in doc["packages"] if p["name"] == "glibc")
    glibc["externalRefs"][-1]["referenceLocator"] = "git+https://sourceware.org/git/glibc.git@0b4e"
    # As iana-etc's: the validator knows LicenseRef-PublicDomain, but not this one.
    next(p for p in doc["packages"] if p["name"] == "tzdata")["licenseDeclared"] = "LicenseRef-IANA"
    (tmp_path / "sbom-erofs.json").write_text(json.dumps(doc))
    (tmp_path / "rootfs-files-erofs.txt").write_bytes(
        (CHECK / "good" / "rootfs-files-erofs.txt").read_bytes()
    )
    fails, _ = sbom_check.check(tmp_path / "sbom-erofs.json")
    assert len(fails) == 2
    assert fails[0].startswith("SPDX: SPDXRef-Package-glibc: externalPackageRef locator of type")
    assert "git+https://sourceware.org/git/glibc.git@0b4e" in fails[0]
    assert fails[1].startswith("SPDX: SPDXRef-Package-tzdata: Unrecognized license reference")


def test_a_document_spdx_tools_cannot_read(tmp_path):
    doc = json.loads((CHECK / "good" / "sbom-erofs.json").read_text())
    doc["packages"][1]["checksums"] = [{"algorithm": "NOPE", "checksumValue": "x"}]
    (tmp_path / "sbom-erofs.json").write_text(json.dumps(doc))
    fails, _ = sbom_check.check(tmp_path / "sbom-erofs.json")
    assert any(f.startswith("SPDX: not readable as SPDX 2.3:") for f in fails)
