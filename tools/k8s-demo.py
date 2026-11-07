#!/usr/bin/env python3
# A multi-node Kubernetes cluster out of the kaetzli image, as a demo:
#
#     ./tools/k8s-demo.py up                  # boot and form the cluster, then return
#     ./tools/k8s-demo.py kubectl get pods -A # kubectl from the host, against it
#     ./tools/k8s-demo.py ssh 2 [cmd...]      # a root shell on k8s-2
#     ./tools/k8s-demo.py ps                  # the guests: running or not, ports, memory
#     ./tools/k8s-demo.py down                # power everything off and delete its state
#
# The code is python/src/kaetzli/k8s_demo.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.k8s_demo")
