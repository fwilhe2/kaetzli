#!/usr/bin/env python3
# Merge the green pull requests update-packages.yml opened:
#
#     ./tools/auto-merge.py        # what .github/workflows/auto-merge.yml runs
#     ./tools/auto-merge.py -n     # say what would happen, touch nothing
#
# The code is python/src/kaetzli/automerge.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.automerge")
