#!/usr/bin/env python3
# Secure Boot seals the root: the signed UKI boots with the command line it carries,
# whatever the VMM passes, and an unsigned one does not boot (tools/sign-uki.sh first).
#
# The code is python/src/kaetzli/boot/secureboot.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.secureboot")
