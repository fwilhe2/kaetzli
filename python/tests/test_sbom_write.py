"""image/sbom.py, the SBOM writer the assembly container runs on its own python3.

fixtures/sbom/expected/ is what the bash image/sbom.sh, with build-rootfs.sh's and
build-extension.sh's document around it, wrote for fixtures/sbom/tree: the same elements
with the same keys in the same order, which re-serialising it with sbom.py's own
serialiser shows to the byte. The layout differs, and JSON escaping is now complete.
Since then, deliberately: a `fix` is the commit's URL, and a LicenseRef- has its text.
"""

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from kaetzli import repo

SCRIPT = repo.ROOT / "image" / "sbom.py"
FIXTURES = Path(__file__).parent / "fixtures" / "sbom"
TREE = FIXTURES / "tree"
CREATED = "2026-01-01T00:00:00Z"

spec = importlib.util.spec_from_file_location("sbom", SCRIPT)
sbom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sbom)

CASES = {
    "image-extra": [
        "image", str(TREE), "--name", "kaetzli-erofs-amd64", "--created", CREATED,
        "--extra", str(FIXTURES / "extra-files.txt"),
        "--unresolved", str(FIXTURES / "unresolved.txt"),
    ],
    "image": [
        "image", str(TREE), "--name", "kaetzli-oci-amd64", "--created", CREATED,
        "--unresolved", "/dev/null",
    ],
    "extension": [
        "extension", str(TREE), "--name", "kaetzli-ext-podman-amd64", "--created", CREATED,
    ],
}  # fmt: skip


@pytest.mark.parametrize("case", CASES)
def test_the_document_is_the_bash_versions(case):
    """Run as the assembly runs it: the file, on a bare python3, with no venv."""
    r = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *CASES[case]],
        capture_output=True,
        text=True,
        check=True,
    )
    golden = json.loads((FIXTURES / "expected" / f"{case}.json").read_text())
    assert r.stdout == sbom.dumps(golden)


def test_stdlib_only():
    """The assembly image has python3 and nothing else: no kaetzli, no third party."""
    tree = ast.parse(SCRIPT.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}


def record(tmp_path, name, text, modules=None):
    d = tmp_path / "usr/share/kaetzli"
    (d / "components").mkdir(parents=True, exist_ok=True)
    (d / "components" / name).write_text(text)
    if modules is not None:
        (d / "modules").mkdir(exist_ok=True)
        (d / "modules" / name).write_text(modules)
    return tmp_path


def test_any_value_is_escaped(tmp_path):
    """jstr escaped backslash, quote, tab and CR; any other control character made the
    document unparseable."""
    odd = 'a\\b"c\td\re\x1bf\x01g ü'
    tree = record(tmp_path, "odd", f"name=odd\nversion={odd}\nlicense={odd}\nurl={odd}\n")
    doc = json.loads(sbom.dumps(sbom.document("image", "n", CREATED, tree)))
    pkg = doc["packages"][1]
    assert (pkg["versionInfo"], pkg["licenseDeclared"], pkg["downloadLocation"]) == (odd,) * 3


def test_spdx_ids_are_letters_digits_dots_and_dashes():
    # tr worked on bytes, so a two-byte character is two dashes.
    assert sbom.spdxid("lib+odd.so.2") == "lib-odd.so.2"
    assert sbom.spdxid("ü/x_y") == "---x-y"


def test_purls():
    assert sbom.purl_path("pkg:golang/github.com/a/b@v1") == "github.com/a/b@v1"
    assert sbom.purl_path("pkg:cargo/serde@1.0") == "serde@1.0"
    p = sbom.module_package("x", "pkg:generic/noversion")
    assert (p["name"], p["versionInfo"]) == ("noversion", "noversion")


def test_records_without_a_name_and_blank_lines_are_skipped(tmp_path):
    tree = record(tmp_path, "a", "name=a\nversion=1\nfix=\n", modules="\npkg:cargo/c@1\n\n")
    record(tmp_path, "b", "version=2\n")
    doc = sbom.document("extension", "n", CREATED, tree)
    assert [p["SPDXID"] for p in doc["packages"]] == [
        "SPDXRef-Extension",
        "SPDXRef-Package-a",
        "SPDXRef-Module-a-c-1",
    ]
    assert len(doc["packages"][1]["externalRefs"]) == 1


def test_a_value_may_contain_and_end_in_equals(tmp_path):
    # bash's `IFS== read` dropped one trailing '=' from a value.
    tree = record(tmp_path, "a", "name=a\nurl=https://x/?a=b=\n")
    assert sbom.document("image", "n", CREATED, tree)["packages"][1]["downloadLocation"] == (
        "https://x/?a=b="
    )


@pytest.mark.parametrize(
    ("locator", "url"),
    [
        (
            "git+https://sourceware.org/git/glibc.git@d6ff274313d79feb864cc10eb775b91c817a67e9",
            "https://sourceware.org/git/?p=glibc.git;a=commit;h=d6ff274313d79feb864cc10eb775b91c817a67e9",
        ),
        (
            "git+https://gcc.gnu.org/git/gcc.git@2ab9ded76e8714b1654260ac68a815c20567cc09",
            "https://gcc.gnu.org/git/?p=gcc.git;a=commit;h=2ab9ded76e8714b1654260ac68a815c20567cc09",
        ),
        ("git+https://github.com/o/r.git@abc1234", "https://github.com/o/r/commit/abc1234"),
        ("git+https://github.com/o/r@abc1234", "https://github.com/o/r/commit/abc1234"),
        # Already a URL (an upstream's patch index), or a form nothing maps: as it is, and
        # test/check-sbom.py says so if it is not a URL.
        ("https://ftp.gnu.org/gnu/bash/bash-5.3-patches/bash53-001", None),
        ("git+https://github.com/fwilhe2/kaetzli#packages/x/patches/0001.patch", None),
    ],
)
def test_a_fix_is_a_url_to_the_commit(locator, url):
    """SPDX 2.3 wants a URL for a `fix`; git+<repo>@<sha> is its download-location
    syntax, which validators refuse there."""
    assert sbom.fix_url(locator) == (url or locator)


def test_each_license_ref_has_its_text(tmp_path):
    """A LicenseRef- is only meaningful with hasExtractedLicensingInfos: the texts the
    packages declaring it ship."""
    tree = record(tmp_path, "tzdata", "name=tzdata\nlicense=LicenseRef-PublicDomain\n")
    record(tmp_path, "openssh", "name=openssh\nlicense=ISC AND LicenseRef-PublicDomain\n")
    record(tmp_path, "openssh-vendor", "name=openssh-vendor\nlicense=LicenseRef-Other\n")
    record(tmp_path, "gawk", "name=gawk\nlicense=GPL-3.0-or-later\n")
    lic = tmp_path / "usr/share/licenses"
    for path, text in {
        "tzdata/LICENSE": "public domain\n",
        "openssh/LICENCE": "isc and pd\n",
        "openssh/sub/NOTE": "more\n",
        "gawk/COPYING": "gpl\n",
    }.items():
        (lic / path).parent.mkdir(parents=True, exist_ok=True)
        (lic / path).write_text(text)
    doc = sbom.document("image", "n", CREATED, tree)
    openssh = "=== openssh/LICENCE ===\nisc and pd\n\n=== openssh/sub/NOTE ===\nmore\n"
    assert doc["hasExtractedLicensingInfos"] == [
        {
            "licenseId": "LicenseRef-Other",
            "name": "Other",
            "extractedText": openssh,
            "comment": "The license texts of openssh-vendor, as usr/share/licenses/ ships them.",
        },
        {
            "licenseId": "LicenseRef-PublicDomain",
            "name": "PublicDomain",
            "extractedText": openssh + "\n=== tzdata/LICENSE ===\npublic domain\n",
            "comment": "The license texts of openssh, tzdata, as usr/share/licenses/ ships them.",
        },
    ]


def test_no_license_ref_no_extracted_infos(tmp_path):
    tree = record(tmp_path, "gawk", "name=gawk\nlicense=GPL-3.0-or-later\n")
    assert "hasExtractedLicensingInfos" not in sbom.document("image", "n", CREATED, tree)


def test_a_license_ref_without_a_text_fails(tmp_path):
    tree = record(tmp_path, "x", "name=x\nlicense=LicenseRef-X\n")
    with pytest.raises(SystemExit, match=r"LicenseRef-X.*usr/share/licenses/x"):
        sbom.document("image", "n", CREATED, tree)


def test_a_builder_is_named_up_to_the_first_colon_and_versioned_after_the_last():
    # As bash's ${b%%:*} and ${b##*:} did, a registry port included.
    p = sbom.builder_package("localhost:5000/kaetzli-builder:dev")
    assert (p["SPDXID"], p["name"], p["versionInfo"]) == (
        "SPDXRef-Builder-localhost-5000-kaetzli-builder-dev",
        "localhost",
        "dev",
    )


def _doc(*declared):
    return {"packages": [{"licenseDeclared": d} for d in declared]}


def test_the_image_license_is_every_packages_expression_anded_once():
    doc = _doc(
        "MIT",
        "GPL-2.0-only AND MIT",
        "NOASSERTION",
        "LGPL-2.1-only OR BSD-2-Clause",
        "GPL-3.0-or-later AND (LGPL-3.0-or-later OR GPL-2.0-or-later)",
        "GPL-3.0-or-later WITH GCC-exception-3.1",
    )
    assert sbom.image_licenses(doc) == (
        "(LGPL-2.1-only OR BSD-2-Clause) AND (LGPL-3.0-or-later OR GPL-2.0-or-later)"
        " AND GPL-2.0-only AND GPL-3.0-or-later AND GPL-3.0-or-later WITH GCC-exception-3.1"
        " AND MIT"
    )


def test_an_image_with_no_declared_license_says_noassertion():
    assert sbom.image_licenses(_doc("NOASSERTION")) == "NOASSERTION"


def test_licenses_prints_the_expression_as_a_json_string(tmp_path, capsys):
    (tmp_path / "s.json").write_text(json.dumps(_doc("Zlib", "MIT")))
    assert sbom.main(["licenses", str(tmp_path / "s.json")]) == 0
    assert capsys.readouterr().out == '"MIT AND Zlib"\n'
