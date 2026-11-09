#!/usr/bin/env python3
# What a Debian snapshot bump changes in the toolchain, as Markdown:
#
#     ./tools/snapshot-changes.py OLD NEW     # e.g. 20261027 20261104
#
# The code is python/src/kaetzli/updates/snapshot_changes.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.updates.snapshot_changes")
