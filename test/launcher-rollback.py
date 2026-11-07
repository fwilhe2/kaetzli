#!/usr/bin/env python3
# The launcher's updates roll back, end to end: a newer snapshot that does not come up is
# marked bad and the machine is started again on the last good one (go/internal/rollout).
#
# The code is python/src/kaetzli/boot/launcher_rollback.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.launcher_rollback")
