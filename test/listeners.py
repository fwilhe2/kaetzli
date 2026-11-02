#!/usr/bin/env python3
# Boot the image and require that what listens on the network is exactly what we expect:
# the base, and with each extension that starts a daemon at boot.
#
# The code is python/src/kaetzli/boot/listeners.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.listeners")
