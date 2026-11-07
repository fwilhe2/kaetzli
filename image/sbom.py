#!/usr/bin/env python3
"""The SBOM, SPDX 2.3, from the component records builder/build-package.sh leaves in a tree.

    sbom.py image     --name N --created T [--extra FILE] [--unresolved FILE] TREE > sbom.json
    sbom.py extension --name N --created T TREE > sbom.json
    sbom.py licenses  SBOM     # one SPDX expression for the whole image, as a JSON string

Run by image/build-rootfs.sh, for the image, and image/build-extension.sh, for each
extension, in the assembly container: its python3 is builder-side, and this file is
stdlib only so that it needs nothing else there (CLAUDE.md, Host-side code). It reads

  TREE/usr/share/kaetzli/components/<pkg>   key=value pins: name, version, license,
                                          origin, builder, url, sha256, cpe, patches,
                                          and one fix=<cve> <patch> <locator> per CVE a
                                          patch fixes
  TREE/usr/share/kaetzli/modules/<pkg>      purls, one per line: the Go modules and Rust
                                          crates a package's programs compiled in

and, for the image, what the caller found out about the tree: the --extra manifest
(tools/build-image.sh --extra) and --unresolved, the libraries a shipped binary needs
and the tree lacks, one per line. test/check-sbom.py checks what comes out.

The document is reproducible: `created` is the caller's, from SOURCE_DATE_EPOCH, the
namespace is a hash of the inputs, and everything is listed in the C locale's order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = "https://github.com/fwilhe2/kaetzli"
NOASSERTION = "NOASSERTION"

KINDS = {
    # kind: (the root element's SPDXID, the tool named as creator)
    "image": ("SPDXRef-Image", "kaetzli-build-rootfs"),
    "extension": ("SPDXRef-Extension", "kaetzli-build-extension"),
}


def spdxid(s: str) -> str:
    """SPDX identifiers may only contain letters, digits, '.' and '-': each other byte is '-'."""
    return re.sub(rb"[^A-Za-z0-9.-]", b"-", s.encode()).decode()


def listing(d: Path) -> list[Path]:
    """The files in d as a shell glob lists them in the C locale: no dotfiles, byte order."""
    if not d.is_dir():
        return []
    return sorted(
        (p for p in d.iterdir() if not p.name.startswith(".")), key=lambda p: p.name.encode()
    )


def split_lines(text: str) -> list[str]:
    """Lines as the shell reads them: split at newline only, not at every character
    str.splitlines() takes for a line break (CR, form feed, U+2028, ...)."""
    return text.removesuffix("\n").split("\n") if text else []


def lines(p: Path) -> list[str]:
    return split_lines(p.read_bytes().decode("utf-8"))  # no newline translation


def read_record(p: Path) -> dict:
    """A component record: the last value of each key, and every fix= in order."""
    r: dict = {"fix": []}
    for line in lines(p):
        key, _, value = line.partition("=")
        if key == "fix":
            r["fix"].append(value)
        else:
            r[key] = value
    return r


def _ordered(*pairs) -> dict:
    """A dict from (key, value) pairs, skipping those whose value is None."""
    return {k: v for k, v in pairs if v is not None}


def no_license(*more) -> list:
    return [
        ("licenseConcluded", NOASSERTION),
        ("licenseDeclared", NOASSERTION),
        ("copyrightText", NOASSERTION),
        *more,
    ]


def component_package(r: dict) -> dict:
    """One package per component record. The purl is pkg:generic, which no advisory
    database indexes; a record with a cpe= gets it as a SECURITY reference, which is what
    scanners match C packages by (NVD's CPE dictionary, env.sh's CPE=)."""
    name, version = r["name"], r.get("version", "")
    refs = [_ref("PACKAGE-MANAGER", "purl", f"pkg:generic/{name}@{version}")]
    # cpe= may name several, space-separated, where NVD files one upstream's advisories
    # under more than one vendor (gawk, shadow); a CPE has no spaces of its own.
    refs += [_ref("SECURITY", "cpe23Type", c) for c in r.get("cpe", "").split()]
    # A CVE one of packages/<pkg>/patches fixes: SPDX's `fix` reference, the code that
    # fixes it, with the CVE first in the comment. test/cve-scan.py reads these back into
    # OpenVEX, so the scan reports the CVE as patched instead of open.
    for fix in r["fix"]:
        f = fix.split(None, 2)
        if not f:
            continue
        cve, file, loc = [*f, "", ""][:3]
        refs.append(
            _ref("SECURITY", "fix", fix_url(loc.rstrip())) | {"comment": f"{cve} fixed by {file}"}
        )
    # The tarball's checksum describes the source before packages/<pkg>/patches/; what
    # was applied on top is said here, each patch by name and SHA-256.
    patches = r.get("patches", "")
    sha256 = r.get("sha256", "")
    return _ordered(
        ("SPDXID", f"SPDXRef-Package-{spdxid(name)}"),
        ("name", name),
        ("versionInfo", version),
        ("downloadLocation", f"git+{REPO}" if r.get("origin") == "local" else r.get("url", "")),
        ("filesAnalyzed", False),
        ("checksums", [{"algorithm": "SHA256", "checksumValue": sha256}] if sha256 else None),
        ("externalRefs", refs),
        ("comment", "patched: " + patches.replace(",", ", ") if patches else None),
        ("licenseConcluded", NOASSERTION),
        ("licenseDeclared", r.get("license") or NOASSERTION),
        ("copyrightText", NOASSERTION),
    )


# tools/patch-cves.sh names an upstream commit as git+<repo>@<sha>, SPDX's syntax for a
# download location; a `fix` reference is a URL. The forms it writes, as the commit's page:
_GITHUB = re.compile(r"git\+(https://github\.com/[^/@#]+/[^/@#]+?)(?:\.git)?@([0-9a-f]{7,40})")
_GITWEB = re.compile(r"git\+https://([^/@#]+)/git/([^/@#]+\.git)@([0-9a-f]{7,40})")


def fix_url(locator: str) -> str:
    """The commit's web page for git+<repo>@<sha>; anything else as it is, so that
    test/check-sbom.py names a form nothing maps yet."""
    if m := _GITHUB.fullmatch(locator):
        return f"{m[1]}/commit/{m[2]}"
    if m := _GITWEB.fullmatch(locator):  # sourceware's and gcc's gitweb
        return f"https://{m[1]}/git/?p={m[2]};a=commit;h={m[3]}"
    return locator


def _ref(category: str, kind: str, locator: str) -> dict:
    return {"referenceCategory": category, "referenceType": kind, "referenceLocator": locator}


def purl_path(purl: str) -> str:
    """What follows pkg:<type>/: name@version."""
    if purl.startswith("pkg:") and "/" in purl[4:]:
        return purl[purl.index("/", 4) + 1 :]
    return purl


def module_id(pkg: str, purl: str) -> str:
    return f"SPDXRef-Module-{spdxid(pkg)}-{spdxid(purl_path(purl))}"


def modules(modules_dir: Path) -> list[tuple[str, str]]:
    """(package, purl) for every Go module and Rust crate, in listing order."""
    return [(lst.name, purl) for lst in listing(modules_dir) for purl in lines(lst) if purl]


def module_package(pkg: str, purl: str) -> dict:
    """One package per Go module or Rust crate a program compiled in, named by its purl,
    which is what scanners match those by."""
    path = purl_path(purl)
    return _ordered(
        ("SPDXID", module_id(pkg, purl)),
        ("name", path.rpartition("@")[0] if "@" in path else path),
        ("versionInfo", path.rpartition("@")[2]),
        ("downloadLocation", NOASSERTION),
        ("filesAnalyzed", False),
        ("externalRefs", [_ref("PACKAGE-MANAGER", "purl", purl)]),
        *no_license(),
    )


def builder_package(b: str) -> dict:
    """The builder images, the one build input env.sh does not describe."""
    return _ordered(
        ("SPDXID", f"SPDXRef-Builder-{spdxid(b)}"),
        ("name", b.partition(":")[0]),
        ("versionInfo", b.rpartition(":")[2]),
        ("downloadLocation", b),
        ("filesAnalyzed", False),
        *no_license(),
    )


def root_package(spdx_id: str, name: str) -> dict:
    """The image or extension itself, which everything else is CONTAINED_BY or a
    DEPENDS_ON of."""
    return _ordered(
        ("SPDXID", spdx_id),
        ("name", name),
        ("downloadLocation", NOASSERTION),
        ("filesAnalyzed", False),
        *no_license(("primaryPackagePurpose", "OPERATING_SYSTEM")),
    )


def extra_package(manifest: bytes) -> dict:
    """Files a --extra overlay added: built elsewhere, so no provenance beyond their paths
    and hashes, which the comment carries."""
    paths = ";".join(split_lines(manifest.decode())).replace(";", "; ")
    return _ordered(
        ("SPDXID", "SPDXRef-LocalExtra"),
        ("name", "local-extra"),
        ("versionInfo", hashlib.sha256(manifest).hexdigest()[:16]),
        ("downloadLocation", NOASSERTION),
        ("filesAnalyzed", False),
        *no_license(
            (
                "comment",
                "Overlaid from a local directory by tools/build-image.sh --extra, "
                f"not built here: {paths}",
            )
        ),
    )


def unresolved_package(lib: str) -> dict:
    """A library a shipped binary needs and the image lacks: DEPENDS_ON without CONTAINS."""
    return _ordered(
        ("SPDXID", f"SPDXRef-Unresolved-{spdxid(lib)}"),
        ("name", lib),
        ("versionInfo", NOASSERTION),
        ("downloadLocation", NOASSERTION),
        ("filesAnalyzed", False),
        *no_license(
            (
                "comment",
                "Referenced by a shipped binary through DT_NEEDED but not present in this image.",
            )
        ),
    )


_LICENSE_REF = re.compile(r"(?<![A-Za-z0-9.:-])LicenseRef-[A-Za-z0-9.-]+")


def extracted_licenses(records: list[dict], tree: Path) -> list[dict]:
    """A LicenseRef- means nothing without its text (SPDX's hasExtractedLicensingInfos):
    for each, the texts every package declaring it ships, in usr/share/licenses/<pkg>/.
    A <pkg>-vendor record's are its package's, as build-rootfs.sh checks them."""
    users: dict[str, list[str]] = {}
    for r in records:
        for ref in sorted(set(_LICENSE_REF.findall(r.get("license", "")))):
            users.setdefault(ref, []).append(r["name"])
    infos = []
    for ref in sorted(users, key=str.encode):
        names = sorted(users[ref], key=str.encode)
        texts = []
        for pkg in sorted({n.removesuffix("-vendor") for n in names}, key=str.encode):
            d = tree / "usr/share/licenses" / pkg
            files = sorted(
                (f for f in d.rglob("*") if f.is_file()) if d.is_dir() else [],
                key=lambda f: str(f.relative_to(d)).encode(),
            )
            if not files:
                raise SystemExit(
                    f"error: {ref} is declared by {pkg}, and usr/share/licenses/{pkg}/ has no text"
                )
            texts += (
                f"=== {pkg}/{f.relative_to(d)} ===\n" + f.read_bytes().decode("utf-8", "replace")
                for f in files
            )
        infos.append(
            {
                "licenseId": ref,
                "name": ref.removeprefix("LicenseRef-"),
                "extractedText": "\n".join(texts),
                "comment": f"The license texts of {', '.join(names)}, "
                "as usr/share/licenses/ ships them.",
            }
        )
    return infos


def rel(a: str, kind: str, b: str) -> dict:
    return {"spdxElementId": a, "relationshipType": kind, "relatedSpdxElement": b}


def document(
    kind: str,
    name: str,
    created: str,
    tree: Path,
    extra: bytes | None = None,
    unresolved: list[str] = (),
) -> dict:
    root, tool = KINDS[kind]
    kaetzli = tree / "usr/share/kaetzli"
    record_files = listing(kaetzli / "components")
    records = [r for r in map(read_record, record_files) if r.get("name")]
    mods = modules(kaetzli / "modules")
    builders = sorted({r["builder"] for r in records if r.get("builder")}, key=str.encode)
    unresolved = [lib for lib in unresolved if lib]

    # Unique per document, and derived rather than random so it is reproducible.
    inputs = b"".join(p.read_bytes() for p in record_files) + (extra or b"")
    namespace = f"{REPO}/spdx/{name}-{hashlib.sha256(inputs).hexdigest()[:16]}"

    packages = [root_package(root, name)]
    packages += map(component_package, records)
    packages += (module_package(pkg, purl) for pkg, purl in mods)
    packages += map(builder_package, builders)
    if extra is not None:
        packages.append(extra_package(extra))
    packages += map(unresolved_package, unresolved)

    # <root> CONTAINS each package, its builder BUILD_TOOL_OF it, and each package
    # CONTAINS its modules.
    rels = [rel("SPDXRef-DOCUMENT", "DESCRIBES", root)]
    for r in records:
        pid = f"SPDXRef-Package-{spdxid(r['name'])}"
        rels.append(rel(root, "CONTAINS", pid))
        if r.get("builder"):
            rels.append(rel(f"SPDXRef-Builder-{spdxid(r['builder'])}", "BUILD_TOOL_OF", pid))
    rels += (
        rel(f"SPDXRef-Package-{spdxid(pkg)}", "CONTAINS", module_id(pkg, purl))
        for pkg, purl in mods
    )
    if extra is not None:
        rels.append(rel(root, "CONTAINS", "SPDXRef-LocalExtra"))
    rels += (rel(root, "DEPENDS_ON", f"SPDXRef-Unresolved-{spdxid(lib)}") for lib in unresolved)

    doc = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": name,
        "documentNamespace": namespace,
        "creationInfo": {
            "created": created,
            "creators": [f"Tool: {tool}", "Organization: github.com/fwilhe2/kaetzli"],
        },
        "documentDescribes": [root],
        "packages": packages,
        "relationships": rels,
    }
    if infos := extracted_licenses(records, tree):
        doc["hasExtractedLicensingInfos"] = infos
    return doc


def dumps(doc: dict) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _and_terms(expr: str) -> list[str]:
    """The operands of an expression's top-level ANDs."""
    terms, depth, start = [], 0, 0
    for i, c in enumerate(expr):
        depth += (c == "(") - (c == ")")
        if depth == 0 and expr.startswith(" AND ", i):
            terms.append(expr[start:i].strip())
            start = i + 5
    return [*terms, expr[start:].strip()]


def _wrapped(t: str) -> bool:
    """Whether one pair of parentheses encloses all of t."""
    depth = 0
    for i, c in enumerate(t):
        depth += (c == "(") - (c == ")")
        if depth == 0:
            return i == len(t) - 1
    return False


def image_licenses(doc: dict) -> str:
    """One SPDX expression for everything a document describes, for the OCI image's
    org.opencontainers.image.licenses: each package's declared expression, AND-ed, every
    operand once. NOASSERTION when no package declares one."""
    terms = set()
    for p in doc.get("packages", []):
        declared = p.get("licenseDeclared") or NOASSERTION
        if declared in (NOASSERTION, "NONE"):
            continue
        for t in _and_terms(declared):
            terms.add(f"({t})" if " OR " in t and not _wrapped(t) else t)
    return " AND ".join(sorted(terms)) or NOASSERTION


def main(argv: list[str]) -> int:
    if argv[:1] == ["licenses"] and len(argv) == 2:
        doc = json.loads(Path(argv[1]).read_text())
        sys.stdout.write(json.dumps(image_licenses(doc)) + "\n")
        return 0
    ap = argparse.ArgumentParser(prog="sbom.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("kind", choices=KINDS)
    ap.add_argument("tree", type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--created", required=True)
    ap.add_argument("--extra", type=Path, help="the --extra overlay's manifest")
    ap.add_argument("--unresolved", type=Path, help="libraries needed and not shipped")
    a = ap.parse_args(argv)
    doc = document(
        a.kind,
        a.name,
        a.created,
        a.tree,
        extra=a.extra.read_bytes() if a.extra else None,
        unresolved=lines(a.unresolved) if a.unresolved else [],
    )
    sys.stdout.write(dumps(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
