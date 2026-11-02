#!/usr/bin/env python3
# Boot the image with a shell as PID 1 and check userspace runs: the boot smoke test.
#
# The code is python/src/kaetzli/boot/smoke.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.smoke")
