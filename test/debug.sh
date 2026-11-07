#!/usr/bin/env bash
# Boot the image with the debug system extension attached and use each tool in it once,
# on something real in the guest:
#
#     ./test/debug.sh [rootfs.erofs] [bzImage]
#
# Overrides: TIMEOUT, MEM/CPUS, LOG, as in test/container.py.
#
# One round per tool, each asking for output only a working tool could produce rather
# than for `--version`, because every one of these can start and still be useless here:
# a strace whose decoders were configured out, a perf trace with no syscall tracepoints
# under it (FTRACE_SYSCALLS, trace.config in packages/kernel/build.sh), a bpftool that
# cannot read what systemd loaded. docs/guides/debugging.md is what they are for; this is the
# check that what it describes still works.
#
#   strace    a decoded uname(2), struct and all
#   tcpdump   an ICMP echo request on lo, captured while ping sends it
#   gdbserver listening on a port with a program stopped under it, which is ptrace
#   socat     a request and an answer over a unix socket
#   jq        a field out of networkctl's JSON
#   bpftool   the cgroup programs systemd attaches at boot
#   perf      a syscall seen by `perf trace`, and a kprobe placed and removed
set -euo pipefail

cd "$(dirname "$0")/.."
source test/qemu-lib.sh

ROOTFS="${1:-${ROOTFS:-output/rootfs.erofs}}"
KERNEL="${2:-${KERNEL:-rootfs/boot/bzImage}}"
SYSEXTS="${SYSEXTS:-debug}"
INIT="${INIT:-/usr/lib/systemd/systemd}"
LOG="${LOG:-output/debug-test.log}"
TEST_NAME=debug

DIAGNOSE="systemd-sysext status; ls -l /usr/bin/strace /usr/bin/perf /usr/bin/bpftool
mount | grep -E 'tracefs|debugfs'; journalctl --no-pager -b -n 40 -o short"

# name : command. Each prints DEBUG-<NAME>-OK when, and only when, it saw what it was
# looking for. As everywhere in test/, the marker is split ('-OK') so the echo of the
# typed command cannot satisfy the wait.
CHECKS=(
    "STRACE:strace -qq -e trace=uname uname 2>&1 >/dev/null | grep -q '^uname({sysname=\"Linux\"' && echo DEBUG-STRACE'-OK'"
    "TCPDUMP:(tcpdump -n -i lo -c 1 icmp > /run/tcpdump.out 2>/dev/null &) ; sleep 2; ping -c 2 -W 1 127.0.0.1 >/dev/null; sleep 1; grep -q 'ICMP echo request' /run/tcpdump.out && echo DEBUG-TCPDUMP'-OK'"
    "GDBSERVER:(gdbserver --once 127.0.0.1:2345 /usr/bin/sleep 30 >/run/gdbserver.out 2>&1 &) ; sleep 2; grep -q 'Listening on port 2345' /run/gdbserver.out && echo DEBUG-GDBSERVER'-OK'; pkill -KILL gdbserver"
    "SOCAT:(socat UNIX-LISTEN:/run/socat.sock SYSTEM:'read l; echo pong-\$l' &) ; sleep 1; [ \"\$(echo ping | socat - UNIX-CONNECT:/run/socat.sock)\" = pong-ping ] && echo DEBUG-SOCAT'-OK'"
    "JQ:networkctl list --json=short | jq -e '.Interfaces[] | select(.Name == \"lo\") | .OperationalState' >/dev/null && echo DEBUG-JQ'-OK'"
    "BPFTOOL:bpftool -j cgroup tree | jq -e '[.. | objects | select(has(\"attach_type\"))] | length > 0' >/dev/null && echo DEBUG-BPFTOOL'-OK'"
    "PERF_TRACE:perf trace -e uname -- uname 2>&1 >/dev/null | grep -q 'uname(' && echo DEBUG-PERF_TRACE'-OK'"
    "PERF_PROBE:perf probe -q -a do_sys_openat2 && perf probe -q -d 'probe:do_sys_openat2' && echo DEBUG-PERF_PROBE'-OK'"
)

qemu_setup
qemu_preflight
qemu_boot
console_login

for check in "${CHECKS[@]}"; do
    name="${check%%:*}"
    echo ">> $name"
    mark=$(console_mark)
    console_send "${check#*:}"
    await "DEBUG-$name-OK" 60 "$mark" || fail "$name: did not see what it was looking for"
done

echo ">> debug extension OK: strace, tcpdump, gdbserver, socat, jq, bpftool, perf"
