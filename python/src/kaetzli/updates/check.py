"""Compare the version each package is pinned to against what upstream has released:

    ./tools/check-updates.py              # every package
    ./tools/check-updates.py bash glibc   # only these
    ./tools/check-updates.py --json       # machine-readable, for the update workflow

Where to look for versions is declared per package in its env.sh; see tools/upstream.py.
A candidate is only reported once its tarball has been confirmed to exist at the URL
env.sh would download it from, so a project that changes its file naming shows up as an
error here instead of as a pull request that fails to build.

A package can have up to three things to keep current, each reported on its own line
and as its own JSON entry, told apart by "kind":

  version   the release itself (VERSION)                 tools/bump-version.sh
  vendor    a second tarball with a VENDOR_VERSION of     tools/bump-version.sh --vendor
            its own, shown as <pkg>/<VENDOR_NAME>
  patches   an upstream patch series (UPSTREAM_PATCHES,   tools/sync-patches.sh
            or a release branch, UPSTREAM_PATCH_GIT),
            shown as <VERSION>-p<n>, n patches

Packages are asked in parallel; the report is in package order either way.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from kaetzli import repo
from kaetzli.updates import release_notes, upstream, versort
from kaetzli.updates.http import Http

WORKERS = 8
# sync-patches.sh fetches a git history into a temporary directory: one at a time, so
# two series (gcc, glibc) are never on disk together.
_PATCH_LISTING = threading.Lock()


@dataclass
class Result:
    lines: list[str] = field(default_factory=list)  # the text report
    errors: list[str] = field(default_factory=list)  # stderr
    failures: list[str] = field(default_factory=list)  # the report's ERROR lines
    updates: list[dict] = field(default_factory=list)
    failed: bool = False

    def line(self, label: str, current: str, rest: str) -> None:
        self.lines.append(f"{label:<12} {current:<10} {rest}")

    def fail(self, label: str, current: str, rest: str, error: str | None = None) -> None:
        self.line(label, current, f"ERROR: {rest}")
        self.failures.append(self.lines[-1])
        if error:
            self.errors.append(f"error: {error}")
        self.failed = True

    def report(self, pkg: str, kind: str, label: str, current: str, latest: str, url: str,
               notes: str | None = None) -> None:  # fmt: skip
        self.lines.append(f"{label:<12} {current:<10} -> {latest:<10} {url}")
        entry = {"package": pkg, "kind": kind, "current": current, "latest": latest, "url": url}
        if kind == "vendor":
            entry["component"] = label.removeprefix(f"{pkg}/")
        if notes:
            entry["release_notes"] = notes
        self.updates.append(entry)


# Every version any env.sh pins, from 1.2.3 to tzdata's 2025b, ca-certificates'
# 20250419+nmu1 and passt's 2025_01_21.4f2c8e7: nothing bash would read as syntax.
VERSION_CHARS = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+~-]*")


def url_for_version(pkg: str, version: str, prefix: str = "") -> str:
    """The URL a package would download if its env.sh said VERSION=<version>. env.sh
    derives PACKAGE, TARBALL and URL from VERSION, so rewriting that one line is all an
    update ever has to do — here and in the pull request the workflow opens. It is
    evaluated by bash, as env.sh always is — so the version, which comes from upstream (a
    tag, a listing), is held to version characters first. upstream.latest's numeric
    filter would stop a quote today, but that is not what it is for."""
    if not VERSION_CHARS.fullmatch(version):
        raise ValueError(f"{version!r} is not a version (expected {VERSION_CHARS.pattern})")
    text = (repo.ROOT / "packages" / pkg / "env.sh").read_text()
    text = re.sub(rf"^{prefix}VERSION=.*$", f'{prefix}VERSION="{version}"', text, flags=re.M)
    out = subprocess.run(
        ["bash", "-c", f'source tools/lib.sh; PKG=$1; eval "$2"; printf %s "${prefix}URL"',
         "bash", pkg, text],
        cwd=repo.ROOT, check=True, capture_output=True, text=True,
    )  # fmt: skip
    return out.stdout


def check_vendor(pkg: str, http: Http, r: Result) -> None:
    name, current = repo.env_get(pkg, "VENDOR_NAME", "VENDOR_VERSION")
    if not current:
        return
    label = f"{pkg}/{name or 'vendor'}"
    try:
        latest = upstream.latest(upstream.Source.of(pkg, vendor=True), http)
    except upstream.UpstreamError as e:
        r.fail(label, current, "upstream lookup failed", str(e))
        return
    if not versort.newer(current, latest):
        r.line(label, current, "up to date")
        return
    url = url_for_version(pkg, latest, "VENDOR_")
    if not http.exists(url):
        r.fail(label, current, f"{latest} announced but {url} is not downloadable")
        return
    r.report(pkg, "vendor", label, current, latest, url)


def check_patches(pkg: str, version: str, r: Result) -> None:
    index, git, branch = repo.env_get(
        pkg, "UPSTREAM_PATCHES", "UPSTREAM_PATCH_GIT", "UPSTREAM_PATCH_BRANCH"
    )
    if not index and not git:
        return
    index = index or f"{git} {branch}"
    ours = sum(
        p.is_file() for p in (repo.ROOT / "packages" / pkg / "patches" / "upstream").rglob("*")
    )
    with _PATCH_LISTING:
        p = subprocess.run(
            ["./tools/sync-patches.sh", "--list", pkg],
            cwd=repo.ROOT,
            capture_output=True,
            text=True,
        )
    if p.returncode != 0:
        r.errors += p.stderr.splitlines()
        r.fail(pkg, f"{version}-p{ours}", "patch listing failed")
        return
    theirs = len(p.stdout.splitlines())
    if theirs <= ours:
        r.line(pkg, f"{version}-p{ours}", "patches up to date")
        return
    r.report(pkg, "patches", pkg, f"{version}-p{ours}", f"{version}-p{theirs}", index)


def check(arg: str, http: Http) -> Result:
    r = Result()
    try:
        pkg = upstream.package_name(arg)
    except upstream.UpstreamError as e:
        r.errors.append(f"error: {e}")
        r.failed = True
        return r
    current, local_source, follows = repo.env_get(pkg, "VERSION", "LOCAL_SOURCE", "FOLLOWS")

    # A package whose source is in this repository has no upstream to compare against —
    # its VERSION is ours, and nothing announces releases of it.
    if local_source:
        r.line(pkg, current, "local source, no upstream")
        return r

    # A package built from another one's tarball (linux-tools from the kernel's) takes its
    # VERSION from that package's env.sh, so the update is that package's to propose.
    # Asking here too would open the same bump twice, and the second pull request would
    # have no VERSION line to rewrite. Its own second tarball is still its own.
    if follows:
        r.line(pkg, current, f"follows {follows}")
        check_vendor(pkg, http, r)
        return r

    check_vendor(pkg, http, r)
    try:
        latest = upstream.latest(upstream.Source.of(pkg), http)
    except upstream.UpstreamError as e:
        r.fail(pkg, current, "upstream lookup failed", str(e))
        return r
    if not versort.newer(current, latest):
        r.line(pkg, current, "up to date")
        # Only now: a new release starts a new series, which its bump syncs anyway.
        check_patches(pkg, current, r)
        return r
    url = url_for_version(pkg, latest)
    if not http.exists(url):
        r.fail(pkg, current, f"{latest} announced but {url} is not downloadable")
        return r
    r.report(pkg, "version", pkg, current, latest, url, release_notes.url(pkg, latest, http))
    return r


def main(argv: list[str], http: Http | None = None) -> int:
    as_json = False
    packages: list[str] = []
    for arg in argv:
        if arg == "--json":
            as_json = True
        elif arg in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        elif arg.startswith("-"):
            print(f"error: unknown option: {arg}", file=sys.stderr)
            return 1
        else:
            packages.append(arg)
    repo.enter_root()
    packages = packages or repo.all_packages()
    http = http or Http()

    failed = False
    updates: list[dict] = []
    with ThreadPoolExecutor(WORKERS) as pool:
        for r in pool.map(lambda p: check(p, http), packages):
            for line in r.errors:
                print(line, file=sys.stderr, flush=True)
            # stdout is the JSON, so the report's ERROR lines, which name the package,
            # go to stderr: a bare curl error there would not say whose upstream failed.
            if as_json:
                for line in r.failures:
                    print(line, file=sys.stderr, flush=True)
            if not as_json:
                for line in r.lines:
                    print(line, flush=True)
            updates += r.updates
            failed |= r.failed
    if as_json:
        print(json.dumps(updates, separators=(",", ":"), ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
