#!/usr/bin/env python3
# Boot the image with systemd and check the network works from inside the guest:
# address, DNS, a route out, and the tools to see them (ip, ping, curl over TLS).
#
# The code is python/src/kaetzli/boot/network.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.network")
