#!/usr/bin/env python3
# Boot with the debug extension and use each tool in it once, on something real: strace,
# tcpdump, gdbserver, socat, jq, bpftool, perf.
#
# The code is python/src/kaetzli/boot/debug.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.debug")
