"""Is the Debian snapshot both images are pinned to still current?

    ./tools/check-snapshot.py           # human readable
    ./tools/check-snapshot.py --json    # the shape update-packages.yml consumes

The toolchain answer to tools/check-updates.py, and deliberately a separate script:
check-updates.py iterates packages/*/env.sh and its contract is "every package", which
the Debian base is not. What the two share is the JSON shape, so the workflow can
concatenate them and open one pull request per entry with no idea which script produced
which.

Any newer dated image is reported. How often that turns into a bump is the caller's
cadence, not this script's: update-packages.yml asks on Sundays only, because every bump
invalidates the builder tag and with it every package's build cache on both
architectures — a full rebuild, worth spending weekly for a current gcc and current
-dev headers and not nightly. (Go and Rust are not sid's: tools/check-toolchains.py.)

  STALE_DAYS    how old the pin has to be before this says so loudly (default 90).
                The failure mode of automation is silence: a checker that stops finding
                updates and a checker that stops running look identical from the outside
                until somebody notices the toolchain is a year old.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from kaetzli import repo
from kaetzli.updates.http import FetchError, Http

HUB_TAGS = "https://hub.docker.com/v2/repositories/library/debian/tags?page_size=100&name=sid-"
_PIN = re.compile(r"ARG SNAPSHOT=([0-9]{8})")
_TAG = re.compile(r"sid-([0-9]{8})")


def pinned_in(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if m := _PIN.match(line):
            return m[1]
    return ""


def latest_tag(http: Http) -> str:
    """The newest dated sid tag, or "" when there is none to be had."""
    try:
        results = json.loads(http.get(HUB_TAGS))["results"]
    except (FetchError, ValueError, KeyError, TypeError):
        return ""
    dates = [m[1] for r in results if (m := _TAG.fullmatch(str(r.get("name", ""))))]
    return max(dates, default="")


def _err(*lines: str) -> None:
    for line in lines:
        print(line, file=sys.stderr)


def check(argv: list[str], root: Path, http: Http, now: datetime, stale_days: int) -> int:
    as_json = False
    if argv[:1] == ["--json"]:
        as_json = True
    elif argv[:1] in (["-h"], ["--help"]):
        print(__doc__.split("\n\n")[1])
        return 0
    elif argv:
        _err(f"error: unknown option: {argv[0]}")
        return 2

    builder = pinned_in(root / "builder" / "Containerfile")
    image = pinned_in(root / "image" / "Containerfile")
    if not builder or not image:
        _err(
            "error: no 'ARG SNAPSHOT=<yyyymmdd>' line in builder/Containerfile"
            " or image/Containerfile"
        )
        return 1
    # Worth checking on its own, and nothing else would: the two images are pinned
    # separately and are supposed to be pinned together. If they drift apart, the
    # toolchain and the thing that lays out the filesystem were built against different
    # Debians, and every size comparison across that boundary measures two changes at once.
    if builder != image:
        _err(
            f"error: builder/Containerfile is pinned to {builder} but image/Containerfile"
            f" to {image}",
            "       they are bumped together; ./tools/bump-snapshot.sh writes both",
        )
        return 1

    current = builder
    pinned = datetime.strptime(current, "%Y%m%d").replace(tzinfo=UTC)
    age_days = int((now - pinned).total_seconds() / 86400)

    latest = latest_tag(http)
    if not latest:
        _err("error: could not list dated sid tags on Docker Hub")
        return 1

    # Said before the up-to-date answer, not after: a pin nobody has moved in three months
    # is worth hearing about whether or not a newer image happens to exist this week.
    if age_days >= stale_days:
        _err(
            f"warning: the Debian snapshot pin is {age_days} days old ({current})",
            "         bumps are proposed automatically, so this means they are not landing",
        )

    if latest <= current:
        if as_json:
            print("[]")
        else:
            print(f"{'debian-snapshot':<16} {current:<10} up to date ({age_days} days old)")
        return 0

    url = f"https://hub.docker.com/_/debian/tags?name=sid-{latest}"
    if as_json:
        entry = {"package": "debian-snapshot", "current": current, "latest": latest, "url": url}
        print(json.dumps([entry], separators=(",", ":")))
    else:
        print(f"{'debian-snapshot':<16} {current:<10} -> {latest:<10} {url}")
    return 0


def main(argv: list[str]) -> int:
    return check(
        argv,
        repo.enter_root(),
        Http(),
        now=datetime.now(UTC),
        stale_days=int(os.environ.get("STALE_DAYS") or 90),
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
