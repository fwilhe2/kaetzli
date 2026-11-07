#!/usr/bin/env python3
# Kubernetes node conformance: the image and its kubernetes extension held to upstream's
# [NodeConformance] node e2e specs, run by ginkgo in the guest (needs KVM).
#
# The code is python/src/kaetzli/boot/node_conformance.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.node_conformance")
