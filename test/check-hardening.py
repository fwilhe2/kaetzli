#!/usr/bin/env python3
# Every package's binaries carry the hardening builder/build-package.sh sets: RELRO,
# BIND_NOW, PIE, no executable stack, a stack protector, FORTIFY_SOURCE.
#
# The code is python/src/kaetzli/elf/hardening.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.elf.hardening")
