#!/usr/bin/env python3
# Boot on the shapes of machine besides qemu's own the kernel is built for: Firecracker's
# (microvm, PVH, virtio-mmio) and Virtualization.framework's (hvc0, virtiofs, vsock).
#
# The code is python/src/kaetzli/boot/vmm.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.boot.vmm")
