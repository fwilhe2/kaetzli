"""test/persistent-var.py: boot the same VM twice with a disk for /var, and check that what
the first boot wrote is there in the second, while the root filesystem stays the same
read-only image.

    ./test/persistent-var.py [rootfs.erofs] [bzImage]

Overrides, from the environment: TIMEOUT, MEM/CPUS, LOGIN_USER, SYSEXTS (default
podman), as in test/podman.py; LOG is the base of the two boots' transcripts
(default output/persistent-var-test, then -first.log and -second.log after it).

The disk is a fresh sparse file each run, so the first boot also proves the part that
only happens once: systemd-makefs putting ext4 on a blank disk (x-systemd.makefs, from
the systemd.mount-extra= Machine passes). What has to survive, and why each:

  a file in /var/lib    the plain case
  files in both homes   /home and /root are symlinks into /var (image/build-rootfs.sh)
  ssh as `user`         with a key the second boot was handed and the first was not: a
                        copy of the boot key kept in the home would refuse it
  a podman image        /var/lib/containers, the reason a persistent /var is wanted
  the journal           Storage=persistent (image/files/etc/systemd/journald.conf.d), so
                        `journalctl --list-boots` sees the previous boot
  the machine ID        from <disk>.machine-id on the kernel command line; with the
                        image's /etc/machine-id empty, every boot would otherwise be a new
                        machine writing a new journal directory

The default, no disk and /var in RAM, is what every other boot test runs on, so it needs
no test of its own here.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from kaetzli.boot import Boot, boot, run
from kaetzli.boot.ssh import host_ssh
from kaetzli.qemu import Guest

RETRY = 5

ON_DISK = "[[ $(findmnt -rno SOURCE,FSTYPE /var) == '/dev/vdb ext4' ]]"


def first_command(machine_id: str) -> str:
    return (
        f"{ON_DISK} && [[ $(</etc/machine-id) == {machine_id} ]] && "
        "cat /proc/sys/kernel/random/boot_id > /var/lib/persistent-var-test && "
        "[[ $(readlink /home):$(readlink /root) == var/home:var/roothome ]] && "
        "runuser -u user -- cp /var/lib/persistent-var-test /home/user/persistent-var-test && "
        "cp /var/lib/persistent-var-test /root/persistent-var-test && "
        "tar -C /usr/share/kaetzli -c sbom.json "
        "| podman import - localhost/persisted >/dev/null && "
        "echo VAR'-FIRST-OK'"
    )


FIRST_DIAGNOSE = (
    "findmnt /var; cat /etc/machine-id; systemctl --no-pager status var.mount "
    "systemd-makefs@*; journalctl --no-pager -b -u 'systemd-makefs*' -n 30"
)


def second_command(machine_id: str) -> str:
    return (
        f"{ON_DISK} && [[ $(</etc/machine-id) == {machine_id} ]] && "
        "first=$(</var/lib/persistent-var-test) "
        "&& [[ $first != $(</proc/sys/kernel/random/boot_id) ]] && "
        "[[ $(</root/persistent-var-test) == $first "
        "&& $(</home/user/persistent-var-test) == $first ]] && "
        "[[ $(stat -c %U /home/user/persistent-var-test) == user ]] && "
        "podman image exists localhost/persisted && "
        "journalctl -q -b -1 -n 1 >/dev/null && "
        "echo VAR'-SECOND-OK'"
    )


SECOND_DIAGNOSE = (
    "findmnt /var; ls -la / /home/user /root; "
    "cat /var/lib/persistent-var-test /proc/sys/kernel/random/boot_id; podman images; "
    "journalctl --no-pager --list-boots"
)

# Whether user's home is the first boot's, asked over ssh.
KEPT = (
    'echo "$(id -un) $([[ $(<~/persistent-var-test) == $(</var/lib/persistent-var-test) ]] '
    '&& echo kept)"'
)


def shutdown(guest: Guest) -> None:
    """Power off and wait for qemu to go: the guest is booted with -no-reboot, so qemu
    exits when it halts, and the second boot must not start until the first has written
    everything back."""
    guest.send("systemctl poweroff")
    until = guest.clock() + 120
    while guest.child.proc.poll() is None:
        if guest.clock() >= until:
            guest.fail("the guest did not power off")
        guest.drain(2)


class Boots:
    def __init__(self, b: Boot, log_base: str):
        self.b = b
        self.log_base = log_base
        env = os.environ
        self.login = env.get("LOGIN_USER") or "root"
        self.port = int(env["SSH_PORT"]) if env.get("SSH_PORT") else None
        self.ssh_dir = ""

    def guest(self, name: str, diagnose: str) -> Guest:
        """A boot of the machine with its own transcript and, as tools/boot-qemu.py makes
        one, a new ssh key, gone with the guest."""
        m = self.b.machine
        m.setup()
        m.preflight()
        self.b.log = f"{self.log_base}-{name}.log"
        self.ssh_dir = tempfile.mkdtemp()
        try:
            m.ssh_setup(self.ssh_dir, port=self.port)
        except BaseException:
            shutil.rmtree(self.ssh_dir, ignore_errors=True)
            raise
        return self.b.guest("persistent-var", diagnose=diagnose, cleanup=[self.ssh_dir])


def from_host(guest: Guest, ssh_dir: str) -> None:
    """As `user`, whose home is now the first boot's. sshd may still be making host keys,
    hence the retries."""
    while True:
        out, rc = host_ssh(ssh_dir, "-o", "ConnectTimeout=10", KEPT)
        if rc == 0 and out == "user kept":
            return
        if guest.clock() >= guest.deadline:
            guest.fail(
                "second boot: ssh as user with this boot's key failed "
                f"(last answer: {out or 'nothing'})"
            )
        if guest.child.proc.poll() is not None:
            guest.fail("qemu exited")
        guest.drain(RETRY)


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="")
    b.machine.sysexts = (os.environ.get("SYSEXTS") or "podman").split()
    log_base = os.environ.get("LOG") or "output/persistent-var-test"
    disk = Path(os.path.dirname(log_base)) / "persistent-var-test.img"
    machine_id_file = Path(f"{disk}.machine-id")
    b.machine.var_disk = str(disk)

    def remove_disk() -> None:
        disk.unlink(missing_ok=True)
        machine_id_file.unlink(missing_ok=True)

    def test() -> None:
        remove_disk()
        boots = Boots(b, log_base)

        with boots.guest("first", FIRST_DIAGNOSE) as guest:
            guest.login(boots.login)
            # Written by the machine as the first boot started.
            want = machine_id_file.read_text()
            print(f">> first boot: /var on a blank disk, machine ID {want}", flush=True)
            guest.send(first_command(want))
            if not guest.wait("VAR-FIRST-OK", 180):
                guest.fail(
                    "first boot: /var is not the disk, the machine ID is wrong, the homes "
                    "are not in /var, or podman could not store an image"
                )
            print(
                ">> first boot OK: /var is ext4 on /dev/vdb, wrote files in /var/lib and "
                "both homes, and a podman image",
                flush=True,
            )
            shutdown(guest)

        with boots.guest("second", SECOND_DIAGNOSE) as guest:
            guest.login(boots.login)
            print(">> second boot: the same disk", flush=True)
            guest.send(second_command(want))
            if not guest.wait("VAR-SECOND-OK", 180):
                guest.fail(
                    "second boot: the first boot's files, image, journal or machine ID "
                    "did not survive"
                )
            print(
                ">> second boot OK: the files, the podman image, the previous boot's "
                "journal and the machine ID all survived",
                flush=True,
            )
            from_host(guest, boots.ssh_dir)
            print(
                ">> second boot ssh OK: this boot's key logged in as user, into the home "
                "the first boot wrote",
                flush=True,
            )
            shutdown(guest)
        remove_disk()

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
