"""What a build changes in the image, against main's build of its base:

    ./test/image-diff.py                    # output/ against main's build of its base
    ./test/image-diff.py OLD [NEW]          # two directories of build output (NEW: output/)
    ./test/image-diff.py --comment          # and keep a comment on the pull request current

The size reports, the SBOMs and the trees' manifests each answer one question about one
build. A version bump, a meson option or a trim line is reviewed by what it does to the
image, which is all three at once and against the image before it: which packages moved,
which paths came or went, and what it weighed. Both sides are what the rootfs job uploads,
`rootfs-size-<arch>` (size reports and manifests) and `sbom-<arch>` (the image's and every
extension's SBOM), so nothing new is stored.

Version bumps rename files (libssl.so.3.5.3 → libssl.so.3.5.4), and a new directory brings
everything under it: both are folded, a rename into one line and a tree into its top.

This never fails a build. It reports; the checks enforce.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from kaetzli import repo
from kaetzli.gh import Gh
from kaetzli.report import Note, Report, run
from kaetzli.size.history import NEWS, moved
from kaetzli.size.sizes import MINUS, SizeReport, delta, mib, read

OUTPUT = "output"
FLAVOURS = ("erofs", "oci")
NONE = "—"
# A pull request comment is limited to 65536 characters; a mass rename should not be what
# stops the rest of the report from arriving.
PATH_LINES = 200
COMMENT_MAX = 60000


@dataclass
class Side:
    # document (disk, oci, or an extension's name) → package → version
    packages: dict[str, dict[str, str]] = field(default_factory=dict)
    # (package, module) → version: the Go modules and Rust crates a package compiled in
    modules: dict[tuple[str, str], str] = field(default_factory=dict)
    files: dict[str, set[str]] = field(default_factory=dict)  # flavour → paths
    sizes: dict[str, SizeReport] = field(default_factory=dict)


def _sbom(side: Side, doc: str, path: Path) -> None:
    data = json.loads(path.read_text())
    names = {}
    pkgs = side.packages.setdefault(doc, {})
    for p in data.get("packages", []):
        sid = p.get("SPDXID", "")
        names[sid] = (p.get("name", ""), p.get("versionInfo", ""))
        if sid.startswith("SPDXRef-Package-"):
            pkgs[p["name"]] = p.get("versionInfo", "")
    for r in data.get("relationships", []):
        b = r.get("relatedSpdxElement", "")
        if r.get("relationshipType") == "CONTAINS" and b.startswith("SPDXRef-Module-"):
            a = r.get("spdxElementId", "")
            # An extension's module may belong to a package its own document names only
            # by id; the id is the name, as image/sbom.py writes it.
            owner = names.get(a, (a.removeprefix("SPDXRef-Package-"), ""))[0]
            name, version = names.get(b, ("", ""))
            if owner and name:
                side.modules[(owner, name)] = version


def load(d: Path) -> Side:
    d, side = Path(d), Side()
    for doc, name in (("disk", "sbom-erofs.json"), ("oci", "sbom-oci.json")):
        if (d / name).is_file():
            _sbom(side, doc, d / name)
    for p in sorted(d.glob("sbom-ext-*.json")):
        _sbom(side, p.name.removeprefix("sbom-ext-").removesuffix(".json"), p)
    for f in FLAVOURS:
        if (d / f"rootfs-files-{f}.txt").is_file():
            text = (d / f"rootfs-files-{f}.txt").read_text()
            side.files[f] = {x for x in text.splitlines() if x and not x.startswith("#")}
        if (d / f"rootfs-size-{f}.txt").is_file():
            side.sizes[f] = read(d / f"rootfs-size-{f}.txt")
    return side


# --- packages ---------------------------------------------------------------------------


def _index(side: Side) -> dict[str, dict[str, str]]:
    """Row key → {document: version}. The image's packages by name, an extension's as
    `<extension>: <name>`, since an extension is attached on its own."""
    out: dict[str, dict[str, str]] = {}
    for doc, pkgs in side.packages.items():
        for name, version in pkgs.items():
            key = name if doc in ("disk", "oci") else f"{doc}: {name}"
            out.setdefault(key, {})[doc] = version
    return out


def _doc_order(doc: str) -> tuple[int, str]:
    return ({"disk": 0, "oci": 1}.get(doc, 2), doc)


def _version(docs: dict[str, str]) -> str:
    return ", ".join(sorted(set(docs.values()))) if docs else NONE


def package_rows(old: Side, new: Side) -> list[tuple[str, str, str, str]]:
    """(package, before, after, where) for each package whose version or place moved.
    where lists the documents it is in, + for one it is new in, a minus sign for one it left."""
    a, b = _index(old), _index(new)
    rows = []
    for key in sorted(a.keys() | b.keys()):
        da, db = a.get(key, {}), b.get(key, {})
        if _version(da) == _version(db) and da.keys() == db.keys():
            continue
        where = ", ".join(
            ("+" if d not in da else MINUS if d not in db else "") + d
            for d in sorted(da.keys() | db.keys(), key=_doc_order)
        )
        rows.append((key, _version(da), _version(db), where))
    return rows


def module_rows(old: Side, new: Side) -> list[tuple[str, str, str, str]]:
    """(package, module, before, after) for the packages that compile modules in on both
    sides: a package that came or went is its own row, not a row per crate."""
    both = {p for p, _ in old.modules} & {p for p, _ in new.modules}
    rows = []
    for key in sorted(old.modules.keys() | new.modules.keys()):
        if key[0] not in both:
            continue
        va, vb = old.modules.get(key, NONE), new.modules.get(key, NONE)
        if va != vb:
            rows.append((*key, va, vb))
    return rows


# --- paths ------------------------------------------------------------------------------


def collapse(paths: set[str]) -> list[tuple[str, int]]:
    """(path, how many paths below it) for each path whose parent is not itself in the
    set: a directory that came or went whole is one line."""
    ordered = sorted(paths)
    shown = []
    for p in ordered:
        parent = p.rpartition("/")[0]
        if parent and parent in paths:
            continue
        lo = bisect.bisect_left(ordered, p + "/")
        hi = bisect.bisect_left(ordered, p + "0")  # '0' sorts right after '/'
        shown.append((p, hi - lo))
    return shown


def _pattern(path: str) -> str:
    return re.sub(r"\d+", "#", path)


@dataclass
class PathChanges:
    added: list[tuple[str, int]]
    removed: list[tuple[str, int]]
    renamed: list[tuple[str, str]]
    n_added: int = 0
    n_removed: int = 0


def path_changes(old: set[str], new: set[str]) -> PathChanges:
    """What came and went, folded. A path that left and one that arrived which differ
    only in their numbers are one rename, when each is the only one of its pattern."""
    added, removed = collapse(new - old), collapse(old - new)

    def unique(entries):
        by = {}
        for path, _ in entries:
            by.setdefault(_pattern(path), []).append(path)
        return {k: v[0] for k, v in by.items() if len(v) == 1}

    ua, ur = unique(added), unique(removed)
    renamed = sorted((ur[k], ua[k]) for k in ur.keys() & ua.keys())
    gone = {p for pair in renamed for p in pair}
    return PathChanges(
        added=[e for e in added if e[0] not in gone],
        removed=[e for e in removed if e[0] not in gone],
        renamed=renamed,
        n_added=len(new - old),
        n_removed=len(old - new),
    )


def diff_lines(c: PathChanges, limit: int = PATH_LINES) -> list[str]:
    def entry(sign, path, below):
        return f"{sign} {path}/  ({_plural(below, 'path')} below)" if below else f"{sign} {path}"

    rows = [(p, entry("+", p, n)) for p, n in c.added]
    rows += [(p, entry("-", p, n)) for p, n in c.removed]
    rows += [(a, f"~ {a} → {b}") for a, b in c.renamed]
    lines = [line for _, line in sorted(rows)]
    if len(lines) > limit:
        lines = [*lines[:limit], f"… and {len(lines) - limit} more"]
    return lines


# --- the report -------------------------------------------------------------------------


class Diff:
    def __init__(self, old: Side, new: Side):
        self.old, self.new = old, new
        self.packages = package_rows(old, new)
        self.modules = module_rows(old, new)
        self.flavours = [f for f in FLAVOURS if f in old.files and f in new.files]
        self.paths = {f: path_changes(old.files[f], new.files[f]) for f in self.flavours}
        self.lines: list[str] = []

    def size_moves(self) -> dict[str, int]:
        return {
            f: self.new.sizes[f].total - self.old.sizes[f].total
            for f in FLAVOURS
            if f in self.old.sizes and f in self.new.sizes
        }

    def n_paths(self) -> int:
        return sum(c.n_added + c.n_removed for c in self.paths.values())

    def changed(self) -> bool:
        return bool(
            self.packages
            or self.modules
            or self.n_paths()
            or any(abs(d) >= NEWS for d in self.size_moves().values())
        )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _packages(d: Diff) -> list[str]:
    lines = ["### Packages", ""]
    if not d.packages:
        lines += ["No package changed.", ""]
    else:
        lines += [
            "| package | before | after | in |",
            "| --- | --- | --- | --- |",
            *(f"| `{k}` | {a} | {b} | {w} |" for k, a, b, w in d.packages),
            "",
            "_in_: the disk image, the OCI image, or an extension; + where it is new, "
            f"{MINUS} where it is gone.",
            "",
        ]
    if d.modules:
        lines += [
            f"<details><summary>Go modules and Rust crates ({len(d.modules)})</summary>",
            "",
            "| package | module | before | after |",
            "| --- | --- | --- | --- |",
            *(f"| `{p}` | `{m}` | {a} | {b} |" for p, m, a, b in d.modules[:PATH_LINES]),
            *(
                [f"| … and {len(d.modules) - PATH_LINES} more | | | |"]
                if len(d.modules) > PATH_LINES
                else []
            ),
            "",
            "</details>",
            "",
        ]
    return lines


def _paths(d: Diff) -> list[str]:
    lines = ["### Paths", ""]
    if not d.flavours:
        return [*lines, "_No path manifests on one side; paths not compared._", ""]
    lines += [
        "| flavour | added | removed | renamed |",
        "| --- | ---: | ---: | ---: |",
    ]
    for f in d.flavours:
        c = d.paths[f]
        r = len(c.renamed)
        lines.append(f"| `{f}` | {c.n_added - r} | {c.n_removed - r} | {r} |")
    lines.append("")
    for f in d.flavours:
        body = diff_lines(d.paths[f])
        if body:
            lines += [
                f"<details><summary>Paths in <code>{f}</code></summary>",
                "",
                "```diff",
                *body,
                "```",
                "",
                "</details>",
                "",
            ]
    return lines


def _sizes(d: Diff) -> list[str]:
    flavours = [f for f in FLAVOURS if f in d.old.sizes and f in d.new.sizes]
    if not flavours:
        return []
    lines = ["### Size", "", "| | before | after | Δ |", "| --- | ---: | ---: | ---: |"]
    for f in flavours:
        a, b = d.old.sizes[f], d.new.sizes[f]
        lines.append(
            f"| `{f}` tree | {mib(a.total)} | {mib(b.total)} | {delta(b.total - a.total)} |"
        )
        packed = "disk" if f == "erofs" else "archive"
        pa, pb = getattr(a, packed), getattr(b, packed)
        if pa and pb:
            lines.append(f"| `{f}` {packed} | {mib(pa)} | {mib(pb)} | {delta(pb - pa)} |")
    lines += ["", "MiB. The tree is apparent size, as `test/rootfs-size.py` measures it.", ""]
    for f in flavours:
        rows = moved(d.new.sizes[f], d.old.sizes[f])
        if rows:
            lines += [
                f"<details><summary>Where <code>{f}</code> changed</summary>",
                "",
                "| directory | after | Δ |",
                "| --- | ---: | ---: |",
                *(f"| `{path}` | {mib(n)} | {delta(dd)} |" for path, n, dd in rows),
                "",
                "</details>",
                "",
            ]
    return lines


def render(old: Side, new: Side, arch: str, against: str) -> list[str]:
    return report_of(Diff(old, new), arch, against)


def report_of(d: Diff, arch: str, against: str) -> list[str]:
    lines = [f"## What this build changes in the image — {arch}", ""]
    if not d.changed():
        return [*lines, f"This build against {against}: No package, path or size changed."]
    moves = d.size_moves()
    counts = (
        (len(d.packages), "package"),
        (len(d.modules), "compiled-in module"),
        (d.n_paths(), "path"),
    )
    head = [_plural(n, word) for n, word in counts if n] or ["no package or path"]
    if "erofs" in moves:
        head.append(f"disk image {delta(moves['erofs'])} MiB")
    lines += [f"This build against {against}: {', '.join(head)}.", ""]
    lines += _packages(d) + _paths(d) + _sizes(d)
    while lines and not lines[-1]:
        lines.pop()
    return lines


# --- against main, and the pull request -------------------------------------------------


# What ci.yml's `changes` job skips the build for: a commit that only touches these has no
# image of its own, and the build before it is its image.
DOCS = re.compile(r"^(docs/.*|[^/]*\.md)$")


def against_main(
    gh: Gh,
    arch: str,
    new_dir: Path,
    rep: Report,
    skip_run: str = "",
    workflow: str = "ci.yml",
    branch: str = "main",
    runs: int = 20,
    *,
    base: str,
) -> Diff:
    """Against main's build of `base`, this branch's merge base (on a push to main, the
    commit before): the green run of that commit, or of an earlier one with only
    documentation after it. Never simply main's newest: once main has moved past the
    base, that build has commits this branch does not, and a diff against it credits the
    branch with them."""
    if problem := gh.problem():
        raise Note(f"{problem}, so there is no build on `{branch}` to compare against.", rep)
    with tempfile.TemporaryDirectory() as tmp:
        for r in gh.successful_runs(workflow, branch, limit=runs):
            if r.id == skip_run:
                continue
            since = ""
            if r.sha != base:
                c = gh.compare(r.sha, base)
                # ahead: the run's commit is an ancestor of the base; 300 is the most files
                # the API lists, so a list that long may not be all of it.
                if c is None or c[0] != "ahead" or len(c[1]) >= 300:
                    continue
                if not all(DOCS.match(f) for f in c[1]):
                    continue
                since = ", documentation only since"
            d = Path(tmp) / r.id
            if not all(gh.download(r.id, f"{a}-{arch}", d) for a in ("rootfs-size", "sbom")):
                continue
            diff = Diff(load(d), load(new_dir))
            what = f"run {r.id} (`{r.sha[:7]}`), the build of this branch's base on `{branch}`"
            if since:
                what = (
                    f"run {r.id} (`{r.sha[:7]}`) on `{branch}`, before this branch's base "
                    f"`{base[:7]}`{since}"
                )
            diff.lines = report_of(diff, arch, what)
            rep.lines(diff.lines)
            return diff
    raise Note(
        f"no green build of this branch's base `{base[:7]}` on `{branch}` with "
        f"rootfs-size-{arch} and sbom-{arch} (still running, failed, or expired): nothing "
        "to compare against. A newer build of main would credit this branch with what main "
        "merged since.",
        rep,
    )


def base_commit(gh: Gh, branch: str, base_branch: str, sha: str) -> str | None:
    """What this build is compared against: on the base branch, the commit before; on any
    other, where it forked from the base branch."""
    if branch == base_branch:
        doc = gh.json(f"commits/{sha}")
        parents = doc.get("parents") if isinstance(doc, dict) else None
        return parents[0]["sha"] if parents else None
    doc = gh.json(f"compare/{base_branch}...{sha}")
    if isinstance(doc, dict) and isinstance(doc.get("merge_base_commit"), dict):
        return doc["merge_base_commit"].get("sha")
    return None


def comment(gh: Gh, branch: str, marker: str, body: str, changed: bool) -> bool:
    """Keep one comment on the branch's pull request current: edited when there is one,
    added only when there is something to say. True when it wrote."""
    pr = gh.open_pr(branch)
    if not pr:
        return False
    text = f"{marker}\n{body}"
    if len(text) > COMMENT_MAX:
        text = text[:COMMENT_MAX] + "\n\n… cut; the run's job summary has all of it."
    if cid := gh.comment_with(pr, marker):
        return gh.edit_comment(cid, text)
    return gh.add_comment(pr, text) if changed else False


def _head() -> str:
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo.ROOT, capture_output=True, text=True)
    return p.stdout.strip()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="image-diff.py", description=__doc__.splitlines()[0])
    ap.add_argument("--arch", default=os.environ.get("ARCH"))
    ap.add_argument("--comment", action="store_true",
                    help="keep a comment on this branch's pull request current")  # fmt: skip
    ap.add_argument("old", nargs="?", help="a directory of build output to compare against")
    ap.add_argument("new", nargs="?", help=f"this build's output (default: {OUTPUT}/)")
    args = ap.parse_args(argv)
    try:
        arch = repo.normalize_arch(args.arch)
    except repo.UnsupportedArch as e:
        ap.error(str(e))
    # Two directories are the caller's; anything else is read from the repository root.
    new_dir = Path(args.new) if args.new else None
    old_dir = Path(args.old) if args.old else None
    if new_dir is None:
        new_dir = repo.ROOT / OUTPUT
    for d in filter(None, (old_dir, new_dir)):
        if not (d / "sbom-erofs.json").is_file():
            print(f"no sbom-erofs.json in {d}/ — build an image first", file=sys.stderr)
            return 2 if args.old else 0

    rep = Report.from_env()
    base = os.environ.get("BASE_BRANCH", "main")
    if old_dir:
        diff = Diff(load(old_dir), load(new_dir))
        diff.lines = report_of(diff, arch, f"`{old_dir}`")
        rep.lines(diff.lines)
    else:
        gh = Gh()
        sha = os.environ.get("GITHUB_SHA") or _head()
        here = os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME", "")
        fork = base_commit(gh, here, base, sha)
        try:
            if fork is None:
                raise Note(f"could not tell where `{sha[:7]}` forked from `{base}`.", rep)
            diff = against_main(
                gh, arch, new_dir, rep,
                skip_run=os.environ.get("GITHUB_RUN_ID", ""), branch=base, base=fork,
            )  # fmt: skip
        except Note as n:
            # A comment from an earlier push compared against something else: say so in it
            # rather than leave it standing. Never a new comment for this.
            if args.comment and here and here != base:
                title = f"## What this build changes in the image — {arch}"
                comment(gh, here, f"<!-- image-diff:{arch} -->", f"{title}\n\n_{n}_", False)
            raise

    branch = os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME", "")
    if args.comment and branch and branch != base:
        body = "\n".join(diff.lines)
        if url := os.environ.get("GITHUB_RUN_ID") and os.environ.get("GITHUB_SERVER_URL"):
            body += (
                f"\n\nRun: {url}/{os.environ.get('GITHUB_REPOSITORY')}"
                f"/actions/runs/{os.environ['GITHUB_RUN_ID']}"
            )
        comment(Gh(), branch, f"<!-- image-diff:{arch} -->", body, diff.changed())
    return 0


if __name__ == "__main__":
    sys.exit(run(main, sys.argv[1:]))
