#!/usr/bin/env python3
# The complete corresponding source of a build: the repository at its commit and that
# commit's sources image (every upstream tarball and patch), verified against the pins:
#
#     ./tools/corresponding-source.py [RELEASE|COMMIT] [--out DIR]
#
# The code is python/src/kaetzli/source.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.source")
