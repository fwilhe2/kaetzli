#!/usr/bin/env python3
# Provision a VM as Gardener provisions a worker: its node init pulls node-agent with ctr,
# and node-agent's bootstrap installs and starts itself.
#
# The code is python/src/kaetzli/boot/gardener.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.gardener")
