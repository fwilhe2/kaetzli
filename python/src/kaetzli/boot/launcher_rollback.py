"""test/launcher-rollback.py: the launcher's updates roll back (go/internal/rollout), end
to end, with the image under test and no network.

    ./test/launcher-rollback.py [rootfs.erofs] [bzImage]

LAUNCHER is the kaetzli binary (default: `go build` of go/cmd/kaetzli into output/).
TIMEOUT bounds each start (default 300).

The launcher's snapshot cache is made by hand: G, the image under test, and B, newer, the
same image with one hex digit of its root hash changed, so its kernel panics on a root it
cannot verify. KAETZLI_RELEASES names a closed port, so the launcher sees no newer release
and boots from the cache. Then, on one machine:

  1. start --snapshot G       comes up; G is its last good snapshot
  2. start --snapshot latest  B is the newest: it panics, is marked bad, and the machine
                              comes up on G again by itself
  3. start                    straight to G: B is skipped, no second failure
  4. rollback                 refused: G is the oldest cached

Each step is the launcher's own output and config.json, so a regression in main.go's
wiring (not only in rollout's decisions, which go test covers) fails here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from kaetzli import repo
from kaetzli.qemu import Refused, refuse

GOOD, BAD = "20260101.000000-1", "20260102.000000-2"
_HASH = re.compile(r"(sha256 )([0-9a-f])([0-9a-f]{63}) ")


class Failed(Exception):
    pass


def break_root_hash(cmdline: str) -> str:
    """The command line with the first digit of its dm-verity root hash changed."""
    m = _HASH.search(cmdline)
    if not m:
        raise ValueError("no dm-verity root hash in the command line")
    digit = "1" if m.group(2) == "0" else "0"
    return cmdline[: m.start(2)] + digit + cmdline[m.end(2) :]


def cache_snapshot(
    cache: Path, name: str, arch: str, rootfs: Path, kernel: Path, cmdline: str
) -> Path:
    """A snapshot as snapshot.Fetch leaves it in the cache."""
    d = cache / f"{name}-{arch}"
    d.mkdir(parents=True)
    for src, dst in ((rootfs, "rootfs.erofs"), (kernel, "bzImage")):
        try:
            os.link(src, d / dst)
        except OSError:
            shutil.copyfile(src, d / dst)
    (d / "rootfs.cmdline").write_text(cmdline)
    (d / "SHA256SUMS").write_text("")  # what Cached looks for; Fetch verified it already
    (d / "snapshot").write_text(f"{name}\n")
    return d


def _launcher() -> str:
    if b := os.environ.get("LAUNCHER"):
        return b
    out = repo.ROOT / "output" / "kaetzli-launcher-test"
    subprocess.run(
        ["go", "build", "-o", str(out), "./cmd/kaetzli"], cwd=repo.ROOT / "go", check=True
    )
    return str(out)


def main(argv: list[str]) -> int:
    repo.enter_root()
    rootfs = Path(argv[0] if argv else os.environ.get("ROOTFS") or "output/rootfs.erofs")
    kernel = Path(argv[1] if len(argv) > 1 else os.environ.get("KERNEL") or "rootfs/boot/bzImage")
    try:
        for p in (rootfs, kernel, rootfs.parent / "rootfs.cmdline"):
            if not p.is_file():
                raise Refused(f"missing {p}")
        launcher = _launcher()
    except Refused as e:
        return refuse(e)

    arch = repo.normalize_arch()
    work = Path(tempfile.mkdtemp(prefix="launcher-rollback-", dir=repo.ROOT / "output"))
    env = {
        **os.environ,
        "KAETZLI_HOME": str(work / "home"),
        "KAETZLI_CACHE": str(work / "cache"),
        "KAETZLI_RELEASES": "http://127.0.0.1:9",
        "TIMEOUT": os.environ.get("TIMEOUT") or "300",
    }
    cmdline = (rootfs.parent / "rootfs.cmdline").read_text()
    cache_snapshot(work / "cache", GOOD, arch, rootfs, kernel, cmdline)
    cache_snapshot(work / "cache", BAD, arch, rootfs, kernel, break_root_hash(cmdline))
    config = work / "home" / "machines" / "t" / "config.json"

    def k(*args: str, ok: bool = True) -> str:
        r = subprocess.run([launcher, *args], env=env, capture_output=True, text=True,
                           timeout=int(env["TIMEOUT"]) * 3)  # fmt: skip
        out = r.stdout + r.stderr
        print(out, end="", flush=True)
        if ok and r.returncode != 0:
            raise Failed(f"`kaetzli {' '.join(args)}` failed ({r.returncode})")
        if not ok and r.returncode == 0:
            raise Failed(f"`kaetzli {' '.join(args)}` succeeded; it should have been refused")
        return out

    def state() -> dict:
        return json.loads(config.read_text())

    def expect(cond: bool, what: str) -> None:
        if not cond:
            raise Failed(f"{what}; config.json: {config.read_text() if config.exists() else '-'}")

    try:
        print(f">> 1: start pinned to {GOOD}", flush=True)
        k("start", "t", "--no-ext", "--snapshot", GOOD)
        expect(state().get("good") == GOOD, f"{GOOD} came up but is not recorded as good")
        k("stop", "t")

        print(f">> 2: follow the newest, {BAD}, whose root hash is wrong", flush=True)
        out = k("start", "t", "--snapshot", "latest")
        expect(f"snapshot {BAD} did not come up" in out, f"{BAD} did not fail as it must")
        expect(f"image: snapshot {GOOD}" in out.split("did not come up", 1)[1],
               f"the machine was not started again on {GOOD}")  # fmt: skip
        s = state()
        expect(s.get("bad") == [BAD] and s.get("good") == GOOD and not s.get("pin"),
               f"after the revert: bad must be [{BAD}], good {GOOD}, no pin")  # fmt: skip
        expect(f"snapshot {GOOD}" in k("info", "t"), f"info does not show {GOOD} running")
        k("stop", "t")

        print(f">> 3: start again: {BAD} is skipped", flush=True)
        out = k("start", "t")
        expect("did not come up" not in out and f"image: snapshot {GOOD}" in out,
               f"a restart did not go straight to {GOOD}")  # fmt: skip
        k("stop", "t")

        print(f">> 4: rollback from {GOOD}, the oldest", flush=True)
        out = k("rollback", "t", ok=False)
        expect("no older snapshot is cached" in out, "rollback's refusal does not say why")
    except (Failed, subprocess.TimeoutExpired) as e:
        print(f">> launcher-rollback test FAILED: {e}", file=sys.stderr, flush=True)
        subprocess.run([launcher, "stop", "t", "--force"], env=env, capture_output=True)
        print(f">> left for a look: {work}", file=sys.stderr)
        return 1
    shutil.rmtree(work)
    print(">> launcher rollback OK: a bad snapshot reverted, was skipped after, rollback guarded")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
