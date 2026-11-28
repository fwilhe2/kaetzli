"""The Gardener release test/gardener.py provisions with (test/gardener.env): is it the
latest, does what is committed match it, and moving it.

    ./tools/check-gardener.py                 # pinned vs Gardener's latest release
    ./tools/check-gardener.py --json          # the shape update-packages.yml consumes
    ./tools/check-gardener.py verify          # test/gardener-init.sh and the digest vs upstream
    ./tools/check-gardener.py bump <version>  # VERSION, NODE_AGENT_DIGEST, gardener-init.sh

Three things move together and only `bump` writes them: VERSION; NODE_AGENT_DIGEST, the
index digest europe-docker.pkg.dev answers for node-agent:<VERSION> (both arches, so the
test pins one reference for either); and test/gardener-init.sh, Gardener's init.tpl.sh at
VERSION rendered by tools/gardener-render.go, Go's text/template with nodeinit's data.
`verify` re-derives the last two from upstream and names whichever differs: a hand edit, a
re-render someone forgot, or a tag that was moved. Needs `go` on PATH and the network.
The JSON entries carry "kind": "gardener", which the workflow turns into `bump`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from kaetzli import repo
from kaetzli.boot import gardener
from kaetzli.updates import versort
from kaetzli.updates.http import FetchError, Http

RELEASES = "https://api.github.com/repos/gardener/gardener/releases/latest"
NOTES = "https://github.com/gardener/gardener/releases/tag/{}"
TEMPLATE = (
    "https://raw.githubusercontent.com/gardener/gardener/{}/"
    "pkg/component/extensions/operatingsystemconfig/nodeinit/templates/scripts/init.tpl.sh"
)
MANIFEST = (
    "https://europe-docker.pkg.dev/v2/gardener-project/releases/gardener/node-agent/manifests/{}"
)
INDEX = [
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
]
TAG = re.compile(r"v\d+\.\d+\.\d+")

Render = Callable[[str, str], str]  # (template, image) -> script


def latest(http: Http) -> str:
    tag = json.loads(http.github(RELEASES))["tag_name"]
    if not TAG.fullmatch(tag):
        raise ValueError(f"latest release tag {tag!r} is not vX.Y.Z")
    return tag


def digest(version: str, http: Http) -> str:
    value = http.header(MANIFEST.format(version), "Docker-Content-Digest", INDEX)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError(f"node-agent:{version}: digest {value!r} is not sha256")
    return value


def go_render(template: str, image: str) -> str:
    """tools/gardener-render.go, by the Go on PATH."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "init.tpl.sh"
        path.write_text(template)
        return subprocess.run(
            ["go", "run", str(repo.ROOT / "tools" / "gardener-render.go"), str(path), image],
            cwd=d,
            env={**os.environ, "GOFLAGS": "-mod=mod", "GO111MODULE": "off"},
            check=True,
            capture_output=True,
            text=True,
        ).stdout


def derive(version: str, http: Http, render: Render) -> tuple[str, str]:
    """(digest, init.sh) for a release, from upstream."""
    d = digest(version, http)
    return d, render(http.get(TEMPLATE.format(version)), gardener.image(version, d))


def report(as_json: bool, root: Path, http: Http) -> int:
    current, _ = gardener.pinned(root)
    try:
        newest = latest(http)
    except (FetchError, ValueError, KeyError) as e:
        print(f"error: gardener: could not read the latest release: {e}", file=sys.stderr)
        return 1
    updates = []
    if versort.newer(current.removeprefix("v"), newest.removeprefix("v")):
        url = NOTES.format(newest)
        updates.append({"package": "gardener", "kind": "gardener", "current": current,
                        "latest": newest, "url": url})  # fmt: skip
        if not as_json:
            print(f"{'gardener':<12} {current:<10} -> {newest:<10} {url}")
    elif not as_json:
        print(f"{'gardener':<12} {current:<10} up to date")
    if as_json:
        print(json.dumps(updates, separators=(",", ":")))
    return 0


def verify(root: Path, http: Http, render: Render) -> int:
    version, pinned_digest = gardener.pinned(root)
    want_digest, want_init = derive(version, http, render)
    wrong = []
    if pinned_digest != want_digest:
        wrong.append(
            f"{gardener.PIN}: NODE_AGENT_DIGEST is {pinned_digest}, but node-agent:{version} "
            f"is {want_digest} (a moved tag?)"
        )
    if (root / gardener.INIT).read_text() != want_init:
        wrong.append(
            f"{gardener.INIT} is not Gardener's init.tpl.sh at {version} as rendered "
            f"(./tools/check-gardener.py bump {version} rewrites it)"
        )
    for line in wrong:
        print(f"error: {line}", file=sys.stderr)
    if not wrong:
        print(f"gardener {version}: digest and {gardener.INIT} match upstream")
    return 1 if wrong else 0


def bump(version: str, root: Path, http: Http, render: Render) -> int:
    if not TAG.fullmatch(version):
        print(f"error: {version!r} is not a Gardener release tag (vX.Y.Z)", file=sys.stderr)
        return 2
    new_digest, new_init = derive(version, http, render)
    pin = root / gardener.PIN
    text = pin.read_text()
    text = re.sub(r'(?m)^VERSION=".*"$', f'VERSION="{version}"', text)
    text = re.sub(r'(?m)^NODE_AGENT_DIGEST=".*"$', f'NODE_AGENT_DIGEST="{new_digest}"', text)
    pin.write_text(text)
    (root / gardener.INIT).write_text(new_init)
    if gardener.pinned(root) != (version, new_digest):
        print(f"error: {gardener.PIN} did not take the new pins", file=sys.stderr)
        return 1
    print(f"gardener {version}: {new_digest}, {gardener.INIT} rendered")
    return 0


def main(argv: list[str], root: Path | None = None, http: Http | None = None,
         render: Render = go_render) -> int:  # fmt: skip
    root = root or repo.enter_root()
    http = http or Http()
    if argv in ([], ["--json"]):
        return report(argv == ["--json"], root, http)
    if argv == ["verify"]:
        return verify(root, http, render)
    if len(argv) == 2 and argv[0] == "bump":
        return bump(argv[1], root, http, render)
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
