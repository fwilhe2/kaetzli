#!/usr/bin/env python3
# Publish a green build of main as a rolling snapshot release (docs/proposals/release.md).
#
# The code is python/src/kaetzli/release.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.release")
