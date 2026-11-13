#!/usr/bin/env python3
# Are the builder's upstream toolchains (builder/toolchains/) at upstream's latest?
#
#     ./tools/check-toolchains.py         # every toolchain
#     ./tools/check-toolchains.py --json  # the shape update-packages.yml consumes
#
# The code is python/src/kaetzli/updates/toolchains.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.updates.toolchains")
