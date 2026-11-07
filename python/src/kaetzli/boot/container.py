"""test/container.py: boot the image with systemd as PID 1 and actually start a container
in the guest, so a kernel config or a crun build that cannot run one fails CI instead of
being discovered by hand.

    ./test/container.py [rootfs.erofs] [bzImage]

Overrides, from the environment:
  TIMEOUT     seconds to wait      (default 300 — qemu falls back to TCG in CI, where
                                    there is no KVM, and that is slow)
  MEM/CPUS    guest size           (default 1024 / 2)
  LOG         console transcript   (default output/container-test.log)
  LOGIN_USER  serial console account (default root; no password, see Guest.login)

Like test/network.py this boots systemd rather than a raw shell: crun asks systemd over
sd-bus to create the cgroup v2 scope, so PID 1 is part of what is under test.

The checks are layered so a failure says where it broke:
  1. cgroup v2 is mounted and the controllers the kernel fragment adds are delegated
  2. crun runs and reports the features it was built with
  3. a container actually starts, and from inside it the pid, uts and mount namespaces
     are demonstrably not the host's

The bundle is built by hand in the guest. `crun spec` writes a complete, valid
config.json, and the only edits needed are patched in with bash parameter expansion:
the image has no jq, and a JSON edit is not a job for grep or sed.
"""

from __future__ import annotations

import os
import sys

from kaetzli.boot import boot, run
from kaetzli.qemu import Guest

# cgroup v2, and specifically the controllers that were not in defconfig: memory is
# MEMCG, and without it a bundle asking for a memory limit fails. cgroup.controllers is
# the delegated set at the root, which is what a container runtime gets to work with.
# Each marker is split by quotes so the echo of what was typed cannot match it.
CGROUP_CHECK = (
    'c=$(</sys/fs/cgroup/cgroup.controllers); echo "controllers: $c"; '
    "[[ $c == *memory* && $c == *pids* && $c == *cpu* ]] && echo CGROUP2'-OK'"
)

# What runs inside the container. Two things constrain how this is written:
#
#   - it ends up as a JSON string in config.json, so it must contain no double quotes and
#     no backslashes, and it is spliced in through a single-quoted shell variable, so it
#     must contain no single quotes either;
#   - it is the *replacement* half of a bash ${var/pat/repl}, where an unescaped & is a
#     backreference to the whole match: `&&` would silently expand to `"sh""sh"`. Hence
#     the nested ifs instead of the `&&` chain the other checks use.
#
# $$ is 1 only in a pid namespace, and bash sets $HOSTNAME from gethostname(2) at
# startup, so it reads `crun` (the hostname the generated spec sets) only in a uts
# namespace. Both are the host's values if crun quietly did nothing.
IN_CTR = (
    "echo ctr-pid=$$; echo ctr-host=$HOSTNAME; "
    "if [[ $$ == 1 ]]; then if [[ $HOSTNAME == crun ]]; then echo CONTAINER-OK; fi; fi"
)

# Bind-mount the guest's own root as the container rootfs: crun needs to pivot_root onto
# a mount point, and this gives the container a populated /usr without any image
# tooling. --bind rather than --rbind on purpose, so the guest's /proc, /sys and /run do
# not come along and the container gets the fresh ones its config.json asks for.
#
# The two patches to the generated spec: terminal false, because there is no console
# socket on this serial line, and args replaced with the assertions above. Both target
# strings occur exactly once in what `crun spec` writes.
#
# The umount is not tidiness, it is a guard: rm -rf crosses mount points, so removing
# /run/ct while /run/ct/rootfs is still a bind mount of / would delete the guest's root
# filesystem through it. A fresh boot has no /run/ct (it is on tmpfs), but this has to
# be safe to run twice in one boot.
BUNDLE = (
    "umount /run/ct/rootfs 2>/dev/null; rm -rf /run/ct && "
    "mkdir -p /run/ct/rootfs && cd /run/ct && crun spec && "
    "mount --bind / /run/ct/rootfs && "
    "c=$(</run/ct/config.json); "
    f'r=\'"/bin/bash", "-c", "{IN_CTR}"\'; '
    "c=${c/'\"terminal\": true'/'\"terminal\": false'}; "
    "c=${c/'\"sh\"'/$r}; "
    "printf '%s' \"$c\" > /run/ct/config.json && echo BUNDLE'-OK'"
)

RUN = 'cd /run/ct && crun run ct-smoke; echo "crun exit: $?"'

FEATURES = "crun --version; echo '--- features ---'; crun features"
DIAGNOSE = (
    "crun list; echo '--- cgroup ---'; cat /sys/fs/cgroup/cgroup.controllers; "
    "echo '--- config ---'; cat /run/ct/config.json; echo '--- dmesg ---'; dmesg | tail -40"
)


def check(guest: Guest) -> None:
    print(">> logged in, checking cgroup v2", flush=True)
    guest.send(CGROUP_CHECK)
    if not guest.wait("CGROUP2-OK", 30):
        guest.fail("cgroup v2 missing a controller the fragment should have added")
    print(">> cgroup v2 OK: memory, pids and cpu are delegated", flush=True)

    # Recorded for the transcript rather than asserted on: what crun was built with is
    # the first thing worth knowing when a bundle behaves unexpectedly.
    guest.send(FEATURES)
    guest.drain(3)

    print(">> building an OCI bundle", flush=True)
    guest.send(BUNDLE)
    if not guest.wait("BUNDLE-OK", 40):
        guest.fail("could not build a bundle (crun spec or the bind mount failed)")

    print(">> starting the container", flush=True)
    guest.send(RUN)
    if not guest.wait("CONTAINER-OK", 60):
        guest.fail("the container did not start or its assertions did not pass")
    print(
        ">> container OK: crun started it, PID 1 in its own pid namespace, /proc its own",
        flush=True,
    )


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="output/container-test.log")

    def test() -> None:
        b.machine.setup()
        b.machine.preflight()
        with b.guest("container", diagnose=DIAGNOSE) as guest:
            guest.login(os.environ.get("LOGIN_USER") or "root")
            check(guest)

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
