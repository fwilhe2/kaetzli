"""test/containerd.py: boot with the kubernetes extension and check containerd as kubelet
and a Gardener node use it: running at boot (Upholds=), its CRI plugin loaded and no
plugin in error, a container started with ctr through the runc v2 shim on the base's
crun, and up before the instance's user data runs. ctr names crun itself (--runc-binary):
BinaryName in the extension's config.toml is CRI configuration, which kubelet's
containers get and ctr's do not.

    ./test/containerd.py [rootfs.erofs] [bzImage]

Overrides, from the environment: TIMEOUT, MEM/CPUS, LOG (default
output/containerd-test.log), as in test/podman.py.

No registry: the container's root is the image's own read-only /, bind-mounted under /run
(crun refuses / itself as a rootfs, as test/container.py says), so nothing is pulled.
There is no runc in the image, so a container that ran at all ran on crun.

The user data, from test/user-data.py's metadata service, is `ctr version`, which fails
unless the daemon answers: a Gardener node's user data pulls its node agent with ctr
before multi-user.target is reached, so containerd has to be up before it (basic.target's
Upholds= and the kaetzli-user-data.service drop-in, packages/containerd/build.sh).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from kaetzli.boot import boot, run
from kaetzli.boot.user_data import metadata_dir, nic
from kaetzli.qemu import Refused

USER_DATA = """#!/bin/bash
ctr version > /run/containerd-user-data 2>&1
echo "exit $?" >> /run/containerd-user-data
"""

CHECK = (
    "systemctl is-system-running --wait >/dev/null; "
    "[[ $(systemctl is-active containerd.service) == active ]] && "
    "ctr plugins ls | grep -E 'io.containerd.grpc.v1 +cri +- +ok' >/dev/null && "
    "! ctr plugins ls | grep -E ' error *$' && "
    "mkdir -p /run/ctr-root && mount --bind / /run/ctr-root && "
    "[[ $(ctr run --rm --rootfs /run/ctr-root --runtime io.containerd.runc.v2 "
    "--runc-binary /usr/bin/crun kaetzli-test /usr/bin/cat /proc/self/cgroup) == 0::/* ]] && "
    "umount /run/ctr-root && "
    "grep -q \"BinaryName = '/usr/bin/crun'\" /etc/containerd/config.toml && "
    "[[ $(systemctl is-active kaetzli-user-data.service) == active ]] && "
    "[[ $(tail -n 1 /run/containerd-user-data) == 'exit 0' ]] && "
    "echo CONTAINERD'-OK'"
)
DIAGNOSE = (
    "systemctl --no-pager status containerd.service kaetzli-user-data.service; "
    "systemd-sysext list; cat /run/containerd-user-data; "
    "journalctl --no-pager -b -u containerd -u kaetzli-user-data -n 40; "
    "ctr plugins ls | grep -v ok"
)


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="output/containerd-test.log")
    root = Path(tempfile.mkdtemp())

    def test() -> None:
        m = b.machine
        m.sysexts = ["kubernetes"]
        m.setup()
        if m.profile != "pc":
            raise Refused(f"test/containerd.py boots the pc profile, not QEMU_PROFILE={m.profile}")
        m.preflight()
        metadata_dir(root)
        (root / "openstack" / "latest" / "user_data").write_text(USER_DATA)
        m.nic_args = nic(root)
        m.append_extra += " kaetzli.user-data=openstack"
        with b.guest("containerd", diagnose=DIAGNOSE) as guest:
            guest.login()
            guest.send(CHECK)
            if not guest.wait("CONTAINERD-OK", 180):
                guest.fail(
                    "containerd is not running, has no CRI plugin, could not run crun, or "
                    "was not up when the user data ran"
                )
            print(
                ">> containerd OK: upheld at boot from the kubernetes extension, CRI plugin "
                "loaded, a container ran through the runc v2 shim on crun, and ctr reached "
                "it from the user data",
                flush=True,
            )

    try:
        return run(test)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
