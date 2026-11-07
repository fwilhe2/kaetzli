#!/usr/bin/env python3
# Boot the image with systemd and start a container in the guest with crun: cgroup v2,
# a bundle made in the guest, and namespaces seen from inside.
#
# The code is python/src/kaetzli/boot/container.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.container")
