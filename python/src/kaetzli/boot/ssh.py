"""test/ssh.py: boot the image with systemd as PID 1 and log into it over ssh, so that a
change which leaves the image with a daemon that will not start, no host key, a PAM stack
that refuses or a tools/ssh.sh that cannot get in fails CI instead of being discovered by
somebody trying to use the machine.

    ./test/ssh.py [rootfs.erofs] [bzImage]

Overrides, from the environment:
  TIMEOUT     seconds to wait                 (default 300 — qemu falls back to TCG in
                                               CI, where there is no KVM, and that is slow)
  MEM/CPUS    guest size                      (default 1024 / 2)
  LOG         console transcript              (default output/ssh-test.log)
  LOGIN_USER  serial console account          (default root; no password, see Guest.login)
  SSH_PORT    host port forwarded to the guest's 22 (default the first free from 2222)
  PREBUILT    directory from `test/prebuilt.sh build`; run its binaries in the guest

Three rounds, layered so a failure says where it broke, the same way test/network.py is.

The first asks whether the *daemon* is there: the unit is active, sshd-keygen.service
left a host key behind, and something is accepting connections on port 22. It uses
nothing but systemctl and bash builtins, so a failure means what it says.

The second asks whether anyone could actually log in, and **the guest connects to
itself**: a key is generated in the guest, authorized, and used over 127.0.0.1. That
goes through everything that could be wrong about sshd in this image (the unit, the host
key, the privilege separation account, the seccomp sandbox, /etc/pam.d/sshd and
pam_systemd) with no forward and no host in the way, which is why it also checks
$XDG_SESSION_ID, the one cheap piece of evidence that the session was registered with
logind rather than merely authenticated.

The third is the way somebody actually uses it: from the host, through tools/ssh.sh,
with the ephemeral key Machine.ssh_setup generated and the guest was handed as a systemd
credential at boot. That is the qemu port forward, the kernel-command-line credential
and the Match block in sshd_config, none of which the second round touches. It runs
*after* the second on purpose: that round overwrote /root/.ssh/authorized_keys with the
guest's own key, so whatever systemd's provision.conf copied there at boot is gone, and
a login now can only have been authorized by the credential where PID 1 put it, in
/run/credentials/@system, which is the path every later boot of the same disk depends
on. It needs an ssh client on the host, which every CI runner image has. That round
passes --root; a last login with tools/ssh.sh's default then has to arrive as `user`
with /run/user/1000, its own session.

A fourth, only when PREBUILT names a directory of binaries test/prebuilt.sh compiled:
copy them in over that same login and run them. It rides on this test because this is
the one that already has a working ssh into the guest, and a boot of its own would cost
minutes under TCG for what is seconds of work.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from kaetzli.boot import boot, run
from kaetzli.qemu import Guest, Refused

# How often a round is tried again until it passes.
RETRY = 5

# Round one. /dev/tcp is bash's own, so this needs no netcat and no ss; asking the socket
# rather than trusting `is-active` is the difference between "systemd started something"
# and "something is listening". The marker is split by quotes so the echo of what was
# typed cannot match it.
DAEMON_CHECK = (
    "[[ $(systemctl is-active sshd.service) == active ]] "
    "&& [[ -s /var/lib/sshd/ssh_host_ed25519_key ]] "
    "&& (exec 3<>/dev/tcp/127.0.0.1/22) "
    "&& echo SSHD-IS'-UP'"
)

# Round two. Idempotent, because it is retried: ssh-keygen refuses to overwrite a key, so
# one is generated only if there is none.
#
# A key is the only way in the shipped sshd_config allows (no account has a password,
# and PasswordAuthentication is off), so this doubles as the check that a key for root
# works under `PermitRootLogin prohibit-password`.
#
# BatchMode=yes so a failed key is an error rather than a password prompt this console
# would sit at until the deadline. StrictHostKeyChecking=no and a /dev/null known-hosts
# file because the host key was generated on this boot and nothing has seen it before.
#
# XDG_SESSION_ID is set by pam_systemd.so out of /etc/pam.d/sshd, and by nothing else in
# a non-interactive ssh command: an empty one means the session never reached logind,
# which is a broken PAM stack wearing a working login as a disguise. Tested for being
# non-empty rather than for looking like a session id: the ids here are `c1`, `c2`, not
# `1`, `2`. logind derives a numeric id from the kernel's audit session id when there is
# one, and packages/kernel/build.sh turns CONFIG_AUDIT off (there is no audit userspace
# here), so it makes its own with a `c` prefix. Asserting a shape would be asserting the
# kernel config from the wrong end of the machine.
_SSH = "ssh -n -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes"
SSH_CHECK = (
    "install -d -m 700 /root/.ssh "
    "&& { [[ -f /root/.ssh/id_ed25519 ]] "
    "|| ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/id_ed25519; } "
    "&& install -m 600 /root/.ssh/id_ed25519.pub /root/.ssh/authorized_keys "
    f"&& [[ $({_SSH} root@127.0.0.1 id -un) == root ]] "
    f"&& [[ -n $({_SSH} root@127.0.0.1 'echo $XDG_SESSION_ID') ]] "
    "&& echo SSH-LOGIN'-OK'"
)

DIAGNOSE = (
    "systemctl --no-pager --full status sshd.service sshd-keygen.service\n"
    "journalctl --no-pager -n 60 -u sshd.service -u sshd-keygen.service\n"
    "ls -l /etc/ssh /var/lib/sshd; getent passwd sshd; ls -ld /usr/share/empty.sshd\n"
    "ls -l /run/credentials/@system; cat /proc/cmdline"
)

# Never on a console: Guest.watch with it waits out its time, watching the guest.
_NOTHING = r"(?!)"


def host_ssh(ssh_dir: str, *args: str) -> tuple[str, int]:
    """tools/ssh.sh's answer (stdout and stderr together, without the final newlines, as
    bash's $(...) has it) and its status."""
    p = subprocess.run(
        ["tools/ssh.sh", *args],
        env={**os.environ, "SSH_DIR": ssh_dir},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return p.stdout.decode(errors="replace").rstrip("\n"), p.returncode


def from_host(guest: Guest, ssh_dir: str, port: int | None) -> None:
    """Rounds three and four: tools/ssh.sh as root, retried, then as user, once."""
    print(
        f">> logging in from the host, through tools/ssh.sh and the forward on port {port}",
        flush=True,
    )
    # `id -un` and whether there is a session id, in one answer, so a single connection
    # proves both. No marker: this is ssh's stdout, not a console that echoes what is
    # typed, and the comparison is exact.
    while True:
        out, rc = host_ssh(
            ssh_dir, "--root", "-o", "ConnectTimeout=10",
            'echo "$(id -un) ${XDG_SESSION_ID:+session}"',
        )  # fmt: skip
        if rc == 0 and out == "root session":
            break
        if guest.clock() >= guest.deadline:
            guest.fail(
                f"tools/ssh.sh could not log in from the host (last answer: {out or 'nothing'})"
            )
        status = guest.watch(_NOTHING, min(RETRY, guest.deadline - guest.clock()))
        if status == "died":
            guest.fail("the guest died")
        if status == "exited":
            guest.fail("qemu exited")
    print(
        ">> ssh from the host OK: the ephemeral key was accepted through the boot credential",
        flush=True,
    )

    # And tools/ssh.sh's default, `user`, through the copy of the same credential
    # kaetzli-home.conf made in /run/ssh-authorized-keys. The runtime directory is the
    # point: it is what rootless podman needs and what `su user` from a root session does
    # not give it, so the answer has to name /run/user/1000, not merely the account.
    out, _ = host_ssh(
        ssh_dir, "-o", "ConnectTimeout=10", 'echo "$(id -un) ${XDG_RUNTIME_DIR:-none}"'
    )
    if out != "user /run/user/1000":
        guest.fail(
            f"tools/ssh.sh did not log in as user with its own session (answer: {out or 'nothing'})"
        )
    print(">> ssh as user OK: its own session, with /run/user/1000", flush=True)

    prebuilt = os.environ.get("PREBUILT")
    if prebuilt:
        print(f">> running binaries built elsewhere, from {prebuilt}", flush=True)
        p = subprocess.run(
            ["./test/prebuilt.sh", "ssh", prebuilt],
            env={**os.environ, "SSH_DIR": ssh_dir},
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if p.returncode != 0:
            guest.fail("a binary built by the host's toolchain does not run in the guest")


def check(guest: Guest, ssh_dir: str, port: int | None) -> None:
    print(">> logged in, checking that sshd is up", flush=True)
    # Retried: sshd-keygen has an RSA key to generate at every boot, and
    # multi-user.target is reached without waiting for it.
    guest.retry(
        DAEMON_CHECK, "SSHD-IS-UP", RETRY,
        "sshd is not running, has no host key, or is not listening",
    )  # fmt: skip
    print(">> sshd OK: unit active, host key present, port 22 accepting connections", flush=True)
    print(">> logging in over ssh, as the machine, to itself", flush=True)
    guest.retry(
        SSH_CHECK, "SSH-LOGIN-OK", RETRY,
        "ssh could not log in (the daemon is up — see the console below)",
    )  # fmt: skip
    print(
        ">> ssh OK: key authentication accepted for root, and logind registered the session",
        flush=True,
    )
    from_host(guest, ssh_dir, port)


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="output/ssh-test.log")
    env = os.environ

    def test() -> None:
        if not shutil.which("ssh"):
            raise Refused("ssh not found (install an openssh client)")
        b.machine.setup()
        b.machine.preflight()
        # Its own directory rather than output/ssh, so running this does not take the key
        # out from under a guest tools/boot-qemu.sh is running. Gone with the guest.
        ssh_dir = tempfile.mkdtemp()
        try:
            port = b.machine.ssh_setup(
                ssh_dir, port=int(env["SSH_PORT"]) if env.get("SSH_PORT") else None
            )
        except BaseException:
            shutil.rmtree(ssh_dir, ignore_errors=True)
            raise
        with b.guest("ssh", diagnose=DIAGNOSE, cleanup=[ssh_dir]) as guest:
            guest.login(env.get("LOGIN_USER") or "root")
            check(guest, ssh_dir, port)

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
