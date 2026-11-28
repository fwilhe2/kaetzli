#!/usr/bin/env python3
# test/gardener.py's node against a real API server (a kubeadm control plane): node-agent's
# certificate, an OSC from a Secret applied, and the Node registered by its kubelet.
#
# The code is python/src/kaetzli/boot/gardener_join.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.gardener_join")
