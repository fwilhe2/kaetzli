#!/usr/bin/env python3
# Boot with system and configuration extensions, from the /var disk and attached as disks:
# merged, what they ship runs, the wrong level is refused, a tampered one fails verity.
#
# The code is python/src/kaetzli/boot/sysext.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.sysext")
