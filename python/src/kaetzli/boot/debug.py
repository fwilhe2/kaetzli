"""test/debug.py: boot the image with the debug system extension attached and use each tool
in it once, on something real in the guest.

    ./test/debug.py [rootfs.erofs] [bzImage]

Overrides, from the environment: TIMEOUT, MEM/CPUS, LOG, LOGIN_USER, as in
test/container.py, and SYSEXTS (default debug).

One round per tool, each asking for output only a working tool could produce rather than
for `--version`, because every one of these can start and still be useless here: a
strace whose decoders were configured out, a perf trace with no syscall tracepoints under
it (FTRACE_SYSCALLS, trace.config in packages/kernel/build.sh), a bpftool that cannot read
what systemd loaded. docs/guides/debugging.md is what they are for; this is the check that
what it describes still works.

  strace    a decoded uname(2), struct and all
  tcpdump   an ICMP echo request on lo, captured while ping sends it
  gdbserver listening on a port with a program stopped under it, which is ptrace
  socat     a request and an answer over a unix socket
  jq        a field out of networkctl's JSON
  bpftool   the cgroup programs systemd attaches at boot
  perf      a syscall seen by `perf trace`, and a kprobe placed and removed
"""

from __future__ import annotations

import os
import sys

from kaetzli.boot import boot, run
from kaetzli.qemu import Guest

DIAGNOSE = (
    "systemd-sysext status; ls -l /usr/bin/strace /usr/bin/perf /usr/bin/bpftool\n"
    "mount | grep -E 'tracefs|debugfs'; journalctl --no-pager -b -n 40 -o short"
)

# (name, command). Each prints DEBUG-<NAME>-OK when, and only when, it saw what it was
# looking for. The marker is split ('-OK') so the echo of the typed command cannot
# satisfy the wait.
CHECKS = [
    (
        "STRACE",
        "strace -qq -e trace=uname uname 2>&1 >/dev/null "
        "| grep -q '^uname({sysname=\"Linux\"' && echo DEBUG-STRACE'-OK'",
    ),
    (
        "TCPDUMP",
        "(tcpdump -n -i lo -c 1 icmp > /run/tcpdump.out 2>/dev/null &) ; sleep 2; "
        "ping -c 2 -W 1 127.0.0.1 >/dev/null; sleep 1; "
        "grep -q 'ICMP echo request' /run/tcpdump.out && echo DEBUG-TCPDUMP'-OK'",
    ),
    (
        "GDBSERVER",
        "(gdbserver --once 127.0.0.1:2345 /usr/bin/sleep 30 >/run/gdbserver.out 2>&1 &) ; "
        "sleep 2; grep -q 'Listening on port 2345' /run/gdbserver.out "
        "&& echo DEBUG-GDBSERVER'-OK'; pkill -KILL gdbserver",
    ),
    (
        "SOCAT",
        "(socat UNIX-LISTEN:/run/socat.sock SYSTEM:'read l; echo pong-$l' &) ; sleep 1; "
        '[ "$(echo ping | socat - UNIX-CONNECT:/run/socat.sock)" = pong-ping ] '
        "&& echo DEBUG-SOCAT'-OK'",
    ),
    (
        "JQ",
        "networkctl list --json=short "
        "| jq -e '.Interfaces[] | select(.Name == \"lo\") | .OperationalState' >/dev/null "
        "&& echo DEBUG-JQ'-OK'",
    ),
    (
        "BPFTOOL",
        "bpftool -j cgroup tree "
        "| jq -e '[.. | objects | select(has(\"attach_type\"))] | length > 0' >/dev/null "
        "&& echo DEBUG-BPFTOOL'-OK'",
    ),
    (
        "PERF_TRACE",
        "perf trace -e uname -- uname 2>&1 >/dev/null | grep -q 'uname(' "
        "&& echo DEBUG-PERF_TRACE'-OK'",
    ),
    (
        "PERF_PROBE",
        "perf probe -q -a do_sys_openat2 && perf probe -q -d 'probe:do_sys_openat2' "
        "&& echo DEBUG-PERF_PROBE'-OK'",
    ),
]


def check(guest: Guest) -> None:
    for name, command in CHECKS:
        print(f">> {name}", flush=True)
        guest.mark()
        guest.send(command)
        if not guest.wait(f"DEBUG-{name}-OK", 60):
            guest.fail(f"{name}: did not see what it was looking for")
    print(">> debug extension OK: strace, tcpdump, gdbserver, socat, jq, bpftool, perf", flush=True)


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="output/debug-test.log")
    b.machine.sysexts = (os.environ.get("SYSEXTS") or "debug").split()

    def test() -> None:
        b.machine.setup()
        b.machine.preflight()
        with b.guest("debug", diagnose=DIAGNOSE) as guest:
            guest.login(os.environ.get("LOGIN_USER") or "root")
            check(guest)

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
