#!/usr/bin/env python3
# Boot with the kubernetes extension and check containerd runs a container through the
# runc v2 shim on crun (packages/containerd).
#
# The code is python/src/kaetzli/boot/containerd.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.containerd")
