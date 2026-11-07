#!/usr/bin/env python3
# Boot a copy of the image with one byte flipped: dm-verity has to report it and the guest
# must never reach a shell.
#
# The code is python/src/kaetzli/boot/verity.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.verity")
