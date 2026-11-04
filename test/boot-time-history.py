#!/usr/bin/env python3
# How long the image takes to boot, against the last builds on main (a job summary).
#
# The code is python/src/kaetzli/boot/timing.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.timing")
