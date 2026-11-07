"""tools/boot-qemu.py: boot an image in qemu once, its serial console in this terminal.

By default nothing is kept, and the ssh key is gone with qemu. For a machine that keeps its
/var and its key across reboots and image updates, see tools/vm.sh.

Overrides, from the environment:
  ARCH     amd64 or arm64          (default the host's own architecture)
  OUT      where the image is      (default output/boot-image, or output/boot-image-$ARCH
                                   if fetch-image.sh was run with a non-default ARCH)
  KERNEL   path to kernel image   (default $OUT/bzImage)
  ROOTFS   path to EROFS image    (default $OUT/rootfs.erofs)
  INIT     PID 1 to run           (default /usr/lib/systemd/systemd)
  MEM      RAM in MB              (default 1024)
  CPUS     vCPUs                  (default 2)
  SSH      0 to boot without the ssh forward and key below   (default 1)
  SSH_PORT host port forwarded to the guest's 22   (default the first free one from 2222)
  SSH_DIR  where the ephemeral key and ssh_config go   (default output/ssh)
  VAR_DISK a file to keep this VM's /var on, created if missing; without it /var is RAM
           and every boot starts empty. VAR_SIZE sizes a new one (default 4G).
           e.g. VAR_DISK=output/var.img
  SYSEXTS  system extensions to attach as read-only disks, a space-separated list, e.g.
           SYSEXTS=kubernetes or SYSEXTS="podman debug":
           EXTENSIONS/extensions/<name>.raw (and confexts/<name>.raw), merged at boot.
           EXTENSIONS defaults to the image's directory
  QEMU_PROFILE  pc (default), microvm (Firecracker's devices; on amd64 KERNEL has to be
           an uncompressed vmlinux) or vz (Virtualization.framework's: hvc0, and
           VIRTIOFS_SOCKET/VSOCK_CID for virtiofs and vsock). See test/vmm.py. vz has no
           qemu monitor on the console, so Ctrl-a x does not exit it: `poweroff` in the
           guest, or kill qemu.
  QEMU_FWCFG_CREDENTIALS, QEMU_REBOOT=1   as for the boot tests (kaetzli.qemu.machine)

The guest gets one virtio-net NIC on qemu's user-mode network (10.0.2.15/24, gateway and
DNS forwarder at 10.0.2.2/10.0.2.3), which systemd-networkd picks up over DHCP. It is
unprivileged and outbound-only apart from one forward, 127.0.0.1:$SSH_PORT to the guest's
sshd.

Log in over ssh, without a password, from another terminal:

    ./tools/ssh.sh                  # a shell as `user`
    ./tools/ssh.sh systemctl status # or a command
    ./tools/ssh.sh --root           # a root shell
    scp -F output/ssh/config file kaetzli:/tmp/

Every boot gets a fresh key pair, handed to the guest as a credential and deleted when
qemu exits. At the console, a username is enough. The guest is kaetzli.qemu's Machine,
the same code as the boot tests. Arguments are passed to qemu as they are.

Drop to a raw shell instead of systemd (handy for debugging a broken boot):
  INIT=/bin/bash ./tools/boot-qemu.py

Exit the guest with Ctrl-a then x.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys

from kaetzli import repo
from kaetzli.qemu.machine import Machine, Refused, refuse


def machine(env=os.environ) -> Machine:
    """The machine as test/qemu-lib.sh's qemu_setup and fetched_image_defaults made it:
    what tools/fetch-image.sh downloaded, unless ROOTFS and KERNEL say otherwise."""
    m = Machine.from_env(
        env, rootfs="", kernel="", init=env.get("INIT") or "/usr/lib/systemd/systemd"
    )
    m.setup()
    out = env.get("OUT") or (
        "output/boot-image" if m.arch == repo.normalize_arch() else f"output/boot-image-{m.arch}"
    )
    m.kernel = env.get("KERNEL") or f"{out}/bzImage"
    m.rootfs = env.get("ROOTFS") or f"{out}/rootfs.erofs"
    m.hint = "(run ./tools/fetch-image.sh)"
    return m


def main(argv: list[str], env=os.environ) -> int:
    repo.enter_root()
    ssh_dir = ""
    try:
        m = machine(env)
        m.preflight()
        if env.get("SSH", "1") == "1":
            ssh_dir = env.get("SSH_DIR") or "output/ssh"
            proxy = env.get("SSH_PROXY", "")
            port = m.ssh_setup(
                ssh_dir,
                port=int(env["SSH_PORT"]) if env.get("SSH_PORT") else None,
                proxy=proxy,
                keep_key=env.get("SSH_KEEP_KEY", "0") == "1",
            )
            reach = f"through {proxy}" if proxy else f"127.0.0.1:{port}"
            print(f">> ssh: ./tools/ssh.sh  ({reach}, key in {ssh_dir}, gone when qemu exits)")
        command = [m.qemu, *m.argv(), *argv]
        sys.stdout.flush()
        # Interactive: the console is this terminal, nothing to drive. Ctrl-C belongs to
        # the guest (or to qemu): a handler, not SIG_IGN, which qemu would inherit. A
        # SIGTERM or a hangup is passed on, so qemu is not left behind with the key gone.
        qemu = subprocess.Popen(command)
        signal.signal(signal.SIGINT, lambda *_: None)
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, lambda signum, _: qemu.send_signal(signum))
        return qemu.wait()
    except Refused as e:
        return refuse(e)
    finally:
        # The ephemeral key goes with the guest.
        if ssh_dir:
            shutil.rmtree(ssh_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
