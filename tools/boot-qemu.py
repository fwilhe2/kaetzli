#!/usr/bin/env python3
# Boot the fetched rootfs + kernel in QEMU, serial console in this terminal (Ctrl-a x
# exits), once: nothing is kept. ./tools/ssh.sh logs in. Overrides (ARCH, OUT, MEM,
# SYSEXTS, VAR_DISK, QEMU_PROFILE, ...) are listed in the code's docstring.
#
# The code is python/src/kaetzli/qemu/interactive.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.qemu.interactive")
