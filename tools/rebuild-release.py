#!/usr/bin/env python3
# Rebuild a snapshot release from its commit, in a clean worktree, the way CI built it, and
# compare every asset with the published SHA256SUMS:
#
#     ./tools/rebuild-release.py 20261107.094545-751 [--no-build]
#
# The code is python/src/kaetzli/rebuild.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.rebuild")
