#!/usr/bin/env python3
# Boot the image with the podman extension and use podman: its build, a container,
# the network from inside one, a published port and a Quadlet unit.
#
# The code is python/src/kaetzli/boot/podman.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.podman")
