#!/usr/bin/env python3
# Boot the image and use each datapath symbol of test/kernel-probes.txt: iptables, nft,
# ip and tc in a network namespace per probe, a failure named by its CONFIG_ symbol.
#
# The code is python/src/kaetzli/boot/kernel_probes.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.kernel_probes")
