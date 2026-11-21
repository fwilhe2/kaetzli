#!/usr/bin/env python3
# Assert the kernel is hardened as test/kernel-hardening.txt says (lockdown, KSPP):
#
#     ./test/kernel-hardening.py [rootfs]   # default rootfs/, reads rootfs/boot/config
#
# The code is python/src/kaetzli/kernel/hardening.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.kernel.hardening")
