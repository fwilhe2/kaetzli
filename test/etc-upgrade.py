#!/usr/bin/env python3
# Two boots from one /var disk, the second with newer extensions: a file nobody wrote in
# /etc follows the update, a written one keeps shadowing it, nothing else is in the upper
# layer.
#
# The code is python/src/kaetzli/boot/etc_upgrade.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.etc_upgrade")
