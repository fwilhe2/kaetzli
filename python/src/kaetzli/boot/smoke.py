"""test/boot.py: boot the image with nobody at the console and check that userspace runs.

    ./test/boot.py [rootfs.erofs] [bzImage]

Overrides, from the environment:
  INIT      PID 1 to run          (default /bin/bash)
  PROMPT    regex the shell's prompt matches, i.e. when it is safe to type
                                  (default bash-[0-9]; change it with INIT)
  TIMEOUT   seconds to wait       (default 300 — qemu falls back to TCG in CI, where
                                   there is no KVM, and that is slow)
  MEM/CPUS  guest size            (default 1024 / 2)
  LOG       console transcript    (default output/boot-test.log)

It drives PID 1 over the serial console: once a shell is up it types a command and waits
for the output to come back. That is a deliberately low bar — it proves the kernel
mounted the root filesystem, exec'd userspace and that the dynamic loader resolved a
real binary's libraries, which is exactly what a bad package update breaks — and keeping
systemd out of it keeps that failure distinguishable from a unit that didn't start.
test/network.py is the one that boots systemd for real.

This is the one test that does not log in: PID 1 *is* the shell, so there is no getty
for Guest.login to talk to, and the loop below types at the prompt directly.
"""

from __future__ import annotations

import os
import sys

from kaetzli.boot import boot, run
from kaetzli.qemu import Guest

# The marker must not appear in the command that produces it: the guest echoes back
# everything typed at the console, and matching our own input would pass every time.
MARKER = "BOOT-SMOKE-OK"
COMMAND = "uname -srm; echo BOOT-SMOKE'-OK'"

# How often the command is typed again until its answer comes back.
RETRY = 2


def smoke(guest: Guest, prompt: str, retry: float = RETRY) -> str:
    """ "ok", or why not.

    Nothing may be typed before the prompt appears, and that is a correctness requirement
    rather than tidiness. arm64's console is a PL011, and qemu stops reading its own
    stdin the moment that model's receive FIFO fills. The FIFO is only drained once the
    guest opens /dev/console and enables receive interrupts — which is what starting a
    shell on it does — so a command line typed before then sits there unread, and qemu
    never resumes: the console is deaf for the rest of the boot. amd64's 16550 recovers
    from the same abuse, which is why typing blind worked while this only ran on amd64.

    After the prompt the command is retried until it lands: the first line can race the
    shell finishing its own startup and come back chewed up, and re-running it is free.
    """
    typed = False
    while (left := guest.deadline - guest.clock()) > 0:
        if not typed:
            status = guest.watch(prompt, left)
            if status == "match":
                typed = True
                continue
        else:
            guest.send(COMMAND)
            status = guest.watch(MARKER, min(retry, left))
            if status == "match":
                return "ok"
        if status in ("died", "exited"):
            return status
    # Worth distinguishing: a boot that never got as far as a prompt is a broken image,
    # while one that printed a prompt and then ignored everything typed at it is a
    # broken console, and the two are debugged in different places.
    return "timeout" if typed else "timeout, no shell prompt"


def _after_marker(log: bytes) -> bytes:
    """grep -F -A2 MARKER | sed 's/^/   /' | head -5"""
    lines = log.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    out: list[bytes] = []
    last = -1
    for i, line in enumerate(lines):
        if MARKER.encode() in line:
            if out and i > last + 1:
                out.append(b"--")
            for j in range(max(i, last + 1), min(i + 3, len(lines))):
                out.append(lines[j])
            last = min(i + 2, len(lines) - 1)
    return b"".join(b"   " + line + b"\n" for line in out[:5])


def main(argv: list[str]) -> int:
    b = boot(argv, init="/bin/bash", log="output/boot-test.log")
    prompt = os.environ.get("PROMPT") or "bash-[0-9]"

    def test() -> None:
        b.machine.setup()
        b.machine.preflight()
        with b.guest("boot") as guest:
            status = smoke(guest, prompt)
            if status != "ok":
                guest.fail(status)
            guest.drain(1)  # the two lines after the marker
            print(">> boot OK", flush=True)
            sys.stdout.buffer.write(_after_marker(guest.log.read_bytes()))
            sys.stdout.flush()

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
