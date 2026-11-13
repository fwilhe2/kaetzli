"""Are the builder's upstream toolchains (builder/toolchains/) at upstream's latest?

    ./tools/check-toolchains.py               # every toolchain, human readable
    ./tools/check-toolchains.py go            # only these
    ./tools/check-toolchains.py --json        # the shape update-packages.yml consumes

tools/check-snapshot.py's sibling: check-updates.py iterates packages/*/env.sh, and Go and
Rust are not packages. The JSON entries carry "kind": "toolchain", which the workflow turns
into tools/bump-toolchain.sh. Latest means upstream's newest stable release: go.dev's
download index, Rust's stable channel manifest.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

from kaetzli import repo
from kaetzli.updates import versort
from kaetzli.updates.http import FetchError, Http

GO_INDEX = "https://go.dev/dl/?mode=json"
RUST_CHANNEL = "https://static.rust-lang.org/dist/channel-rust-stable.toml"


def latest_go(http: Http) -> str:
    for release in json.loads(http.get(GO_INDEX)):
        if release.get("stable"):
            return str(release["version"]).removeprefix("go")
    raise ValueError("no stable Go release listed")


def latest_rust(http: Http) -> str:
    return tomllib.loads(http.get(RUST_CHANNEL))["pkg"]["rustc"]["version"].split()[0]


LATEST: dict[str, Callable[[Http], str]] = {"go": latest_go, "rust": latest_rust}
NOTES = {
    "go": "https://go.dev/doc/devel/release#go{}",
    "rust": "https://github.com/rust-lang/rust/releases/tag/{}",
}


def names(root: Path) -> list[str]:
    return sorted(p.stem for p in (root / "builder" / "toolchains").glob("*.sh"))


def pinned(root: Path, name: str) -> str:
    """VERSION from the pin file, read by bash as everything else reads it."""
    script = 'source "$1"; printf %s "$VERSION"'
    return subprocess.run(
        ["bash", "-c", script, "bash", f"builder/toolchains/{name}.sh"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def check(argv: list[str], root: Path, http: Http) -> int:
    as_json = "--json" in argv
    wanted = [a for a in argv if a != "--json"]
    if any(a.startswith("-") for a in wanted):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    known = names(root)
    for name in wanted:
        if name not in known:
            print(f"error: unknown toolchain '{name}' (no builder/toolchains/{name}.sh)",
                  file=sys.stderr)  # fmt: skip
            return 2

    updates, failed = [], False
    for name in wanted or known:
        current = pinned(root, name)
        try:
            latest = LATEST[name](http)
        except (FetchError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            print(f"error: {name}: could not read the latest release from upstream",
                  file=sys.stderr)  # fmt: skip
            failed = True
            continue
        if not versort.newer(current, latest):
            if not as_json:
                print(f"{name:<12} {current:<10} up to date")
            continue
        url = NOTES[name].format(latest)
        updates.append({"package": name, "kind": "toolchain", "current": current,
                        "latest": latest, "url": url})  # fmt: skip
        if not as_json:
            print(f"{name:<12} {current:<10} -> {latest:<10} {url}")
    if as_json:
        print(json.dumps(updates, separators=(",", ":")))
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    return check(argv, repo.enter_root(), Http())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
