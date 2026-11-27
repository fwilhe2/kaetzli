#!/usr/bin/env python3
# Boot as an OpenStack instance, with a metadata service, and check the instance's user
# data ran as root, once (packages/user-data).
#
# The code is python/src/kaetzli/boot/user_data.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.user_data")
