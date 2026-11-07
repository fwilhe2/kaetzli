#!/usr/bin/env python3
# Boot the same machine twice with a disk for /var: what the first boot wrote, the second
# must still have, the homes, a podman image, the journal and the machine ID among it.
#
# The code is python/src/kaetzli/boot/persistent_var.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.persistent_var")
