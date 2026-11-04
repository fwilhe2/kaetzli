#!/usr/bin/env python3
# Publish a green build of main as a GitHub release (docs/proposals/release.md):
#
#     ./tools/release.py name                 # the snapshot name for this commit and run
#     ./tools/release.py publish --dry-run    # read everything, say what it would do
#     ./tools/release.py publish              # what ci.yml's release job runs
#
# The code is python/src/kaetzli/release.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.release")
