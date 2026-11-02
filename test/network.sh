#!/usr/bin/env bash
# Boot the image with systemd as PID 1 and check that the network actually works from
# inside the guest, so a change that leaves the image without an address, without DNS
# or without a route fails CI instead of being discovered by hand.
#
#     ./test/network.sh [rootfs.erofs] [bzImage]
#
# Overrides:
#   HOST/PORT  what to resolve and connect to  (default example.com / 80)
#   TIMEOUT    seconds to wait                 (default 300 — qemu falls back to TCG in
#                                              CI, where there is no KVM, and that is slow)
#   MEM/CPUS   guest size                      (default 1024 / 2)
#   LOG        console transcript              (default network-test.log)
#   LOGIN_USER                 serial console account (default root; no password, see console_login)
#
# Unlike test/boot.py this has to run systemd: the addressing is systemd-networkd's job
# and the resolver is systemd-resolved's, so a raw shell as PID 1 would prove nothing.
# That means logging in at the serial getty first, which takes no password when the
# login.noauth credential is passed — test/qemu-lib.sh's console_login does that part.
#
# The checks come in two rounds, layered so that a failure says where it broke.
#
# The first round asks whether the *system* has a network, using nothing the image had to
# grow a package for: a routable link means DHCP answered, `getent hosts` means the
# systemd-resolved stub that /etc/resolv.conf points at answers, and opening a TCP
# connection means routing and the VMM's NAT work.
#
# The second asks whether anyone logged in could *see and use* it, which is what issue
# #77 was about: `ip` reads the address and route back out of the kernel rather than out
# of networkd's opinion of them, `ping` proves an ICMP socket opens, and `curl` proves
# TLS — name resolution, a chain verified against the shipped CA bundle, and a request.
# Splitting them means "the network is broken" and "the tools are broken" cannot be
# mistaken for one another.
#
# Everything past the first check needs the machine running this to have internet access
# — with qemu's user-mode networking the guest reaches the outside through it.
set -euo pipefail

cd "$(dirname "$0")/.."
source test/qemu-lib.sh

ROOTFS="${1:-${ROOTFS:-output/rootfs.erofs}}"
KERNEL="${2:-${KERNEL:-rootfs/boot/bzImage}}"
INIT="${INIT:-/usr/lib/systemd/systemd}"
HOST="${HOST:-example.com}"
PORT="${PORT:-80}"
LOG="${LOG:-output/network-test.log}"
TEST_NAME=network

# Same trick as the READY marker in test/qemu-lib.sh: the guest echoes back everything
# typed at the console, so a marker must not appear in the command that produces it or we
# would match our own input. The guest's shell strips the quotes; the patterns below are
# the joined string.
#
# The first round uses nothing but bash builtins, networkctl and getent. That started as
# a constraint — the image had no grep, sed or awk — and is now a choice: bash's [[ ]]
# and /dev/tcp cover what they would have been for, and a console handshake this
# delicate is better off not depending on a package it is not testing. The second round
# is the one place that rule is deliberately broken, because the tools *are* what it is
# testing; it still parses their output with [[ ]] rather than reaching for grep.
MARKER="NET-SMOKE-OK"
TOOLS_MARKER="NET-TOOLS-OK"
CHECK="[[ \$(networkctl --no-pager) == *routable* ]] \
&& getent hosts $HOST >/dev/null \
&& (exec 3<>/dev/tcp/$HOST/$PORT) \
&& echo NET-SMOKE'-OK'"
# -oneline so the address and its flags land on one line for [[ ]] to match, and -4 so
# the link-local IPv6 address a NIC has whether or not anything configured it cannot
# satisfy this on its own.
#
# ping goes to the loopback address rather than $HOST on purpose: qemu's user-mode
# networking only forwards ICMP when the *host* kernel lets an unprivileged process open
# a datagram ICMP socket, which is a property of the machine running the test and not of
# the image. 127.0.0.1 asks the question this test can answer — that the binary runs and
# gets its socket — and leaves reachability to the TCP and TLS checks either side of it.
#
# curl carries the whole TLS stack on its back: resolving the name, verifying the chain
# against /etc/ssl/certs/ca-certificates.crt, and speaking HTTP over it. -f so an HTTP
# error status is a failure rather than a zero-length body, -sS so only errors print.
TOOLS_CHECK="[[ \$(ip -4 -oneline address show scope global) == *inet* ]] \
&& [[ \$(ip -4 route show default) == default* ]] \
&& ping -c1 -W5 127.0.0.1 >/dev/null \
&& curl -fsS -o /dev/null https://$HOST/ \
&& echo NET-TOOLS'-OK'"
DIAGNOSE="networkctl --no-pager status; resolvectl --no-pager status; cat /etc/resolv.conf
ip -4 address show; ip -4 route show"

qemu_setup
qemu_preflight
qemu_boot
console_login

echo ">> logged in, checking the network"

# Retried until it passes: DHCP takes a moment to complete, and re-running the checks
# costs nothing.
until grep -qaF "$MARKER" "$LOG"; do
    [ "$SECONDS" -lt "$deadline" ] || fail "no address, no DNS or no route out (timed out)"
    grep -qaE "$QEMU_DIED" "$LOG" && fail "the guest died"
    kill -0 "$qemu_pid" 2>/dev/null || fail "qemu exited"
    console_send "$CHECK"
    sleep 5
done

echo ">> network OK: link routable, $HOST resolved, TCP connection to $HOST:$PORT accepted"
echo ">> checking the tools a person logged in here would reach for"

# Round two. Nothing above needs retrying to reach here, so anything this round is
# waiting for is a TLS handshake or a slow name lookup rather than a lease — but a few
# more attempts cost nothing next to failing a CI run on one dropped packet.
until grep -qaF "$TOOLS_MARKER" "$LOG"; do
    [ "$SECONDS" -lt "$deadline" ] || \
        fail "ip, ping or curl failed (the network itself is up — see the console below)"
    grep -qaE "$QEMU_DIED" "$LOG" && fail "the guest died"
    kill -0 "$qemu_pid" 2>/dev/null || fail "qemu exited"
    console_send "$TOOLS_CHECK"
    sleep 5
done

echo ">> tools OK: ip shows the lease and a default route, ping runs, curl https://$HOST verified"
