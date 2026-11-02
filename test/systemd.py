#!/usr/bin/env python3
# Boot the image with systemd and require it comes up clean: no failed units, no taint,
# no journal errors.
#
# The code is python/src/kaetzli/boot/systemd.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.systemd")
