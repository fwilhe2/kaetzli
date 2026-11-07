"""test/k8s.py: a three-node cluster through tools/k8s-demo.py (kaetzli.k8s_demo), up, its
guests listed, and down again, so that a change which breaks kubeadm, CRI-O, the pod
network or the Service across nodes fails CI.

    ./test/k8s.py [rootfs.erofs] [bzImage]

The overrides are tools/k8s-demo.py's. It needs KVM: three emulated guests forming a
cluster would take hours. Without a writable /dev/kvm it skips, with a note. Each guest's
console is kept as output/k8s-test-<guest>.log, since `down` deletes output/k8s.
"""

from __future__ import annotations

import os
import shutil
import sys

from kaetzli import k8s_demo, repo
from kaetzli.qemu.machine import kvm_usable


def keep_logs() -> None:
    for log in sorted(k8s_demo.STATE.glob("*.console.log")):
        shutil.copyfile(log, f"output/k8s-test-{log.name.removesuffix('.console.log')}.log")
    k8s_demo.down()


def main(argv: list[str]) -> int:
    repo.enter_root()
    env = os.environ
    env["ROOTFS"] = argv[0] if argv else env.get("ROOTFS") or "output/rootfs.erofs"
    env["KERNEL"] = argv[1] if len(argv) > 1 else env.get("KERNEL") or "rootfs/boot/bzImage"
    if not kvm_usable():
        print("note: no writable /dev/kvm on this host, skipping the Kubernetes check")
        return 0
    try:
        for command in ("up", "ps"):
            rc = k8s_demo.main([command])
            if rc:
                return rc
        print(">> k8s OK")
        return 0
    finally:
        keep_logs()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
