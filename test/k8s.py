#!/usr/bin/env python3
# A three-node Kubernetes cluster through tools/k8s-demo.py: up, its guests listed, down
# again (needs KVM).
#
# The code is python/src/kaetzli/boot/k8s.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.k8s")
