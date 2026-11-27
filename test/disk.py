#!/usr/bin/env python3
# Boot a disk from tools/build-disk.sh under UEFI firmware, as a VMM or cloud would
# (DISK_BUS=virtio|scsi).
#
# The code is python/src/kaetzli/boot/disk.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.disk")
