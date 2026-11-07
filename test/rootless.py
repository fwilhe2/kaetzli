#!/usr/bin/env python3
# Boot the image with the podman extension, log in as user and run containers without
# root: the ID-map helpers, the maps, and pasta.
#
# The code is python/src/kaetzli/boot/rootless.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.rootless")
