#!/usr/bin/env bash
# Boot the image with systemd as PID 1 and log into it over ssh, so that a change which
# leaves the image with a daemon that will not start, no host key, a PAM stack that
# refuses or a tools/ssh.sh that cannot get in fails CI instead of being discovered by
# somebody trying to use the machine.
#
#     ./test/ssh.sh [rootfs.erofs] [bzImage]
#
# Overrides:
#   TIMEOUT    seconds to wait                 (default 300 — qemu falls back to TCG in
#                                              CI, where there is no KVM, and that is slow)
#   MEM/CPUS   guest size                      (default 1024 / 2)
#   LOG        console transcript              (default ssh-test.log)
#   LOGIN_USER                 serial console account (default root; no password, see console_login)
#   SSH_PORT   host port forwarded to the guest's 22 (default the first free from 2222)
#   PREBUILT   directory from `test/prebuilt.sh build`; run its binaries in the guest
#
# Three rounds, layered so a failure says where it broke, the same way test/network.py is.
#
# The first asks whether the *daemon* is there — the unit is active, sshd-keygen.service
# left a host key behind, and something is accepting connections on port 22. It uses
# nothing but systemctl and bash builtins, so a failure means what it says.
#
# The second asks whether anyone could actually log in, and **the guest connects to
# itself**: a key is generated in the guest, authorized, and used over 127.0.0.1. That
# goes through everything that could be wrong about sshd in this image — the unit, the
# host key, the privilege separation account, the seccomp sandbox, /etc/pam.d/sshd and
# pam_systemd — with no forward and no host in the way, which is why it also checks
# $XDG_SESSION_ID, the one cheap piece of evidence that the session was registered with
# logind rather than merely authenticated.
#
# The third is the way somebody actually uses it: from the host, through tools/ssh.sh,
# with the ephemeral key qemu_ssh_setup generated and the guest was handed as a systemd
# credential at boot. That is the qemu port forward, the kernel-command-line credential
# and the Match block in sshd_config — none of which the second round touches. It runs
# *after* the second on purpose: that round overwrote /root/.ssh/authorized_keys with the
# guest's own key, so whatever systemd's provision.conf copied there at boot is gone, and
# a login now can only have been authorized by the credential where PID 1 put it, in
# /run/credentials/@system. Which is the path every later boot of the same disk depends
# on. It needs an ssh client on the host, which every CI runner image has. That round
# passes --root; a last login with tools/ssh.sh's default then has to arrive as `user`
# with /run/user/1000, its own session.
#
# A fourth, only when PREBUILT names a directory of binaries test/prebuilt.sh compiled:
# copy them in over that same login and run them. It rides on this test because this is
# the one that already has a working ssh into the guest, and a boot of its own would
# cost minutes under TCG for what is seconds of work.
set -euo pipefail

cd "$(dirname "$0")/.."
source test/qemu-lib.sh

ROOTFS="${1:-${ROOTFS:-output/rootfs.erofs}}"
KERNEL="${2:-${KERNEL:-rootfs/boot/bzImage}}"
INIT="${INIT:-/usr/lib/systemd/systemd}"
LOG="${LOG:-output/ssh-test.log}"
TEST_NAME=ssh

# Same marker trick as test/qemu-lib.sh and test/network.py: the guest echoes back what is
# typed at the console until `stty -echo` has taken effect, so a marker must not appear
# verbatim in the command that produces it. The guest's shell strips the quotes.
DAEMON_MARKER="SSHD-IS-UP"
LOGIN_MARKER="SSH-LOGIN-OK"

# Round one. /dev/tcp is bash's own, so this needs no netcat and no ss — and asking the
# socket rather than trusting `is-active` is the difference between "systemd started
# something" and "something is listening".
DAEMON_CHECK="[[ \$(systemctl is-active sshd.service) == active ]] \
&& [[ -s /var/lib/sshd/ssh_host_ed25519_key ]] \
&& (exec 3<>/dev/tcp/127.0.0.1/22) \
&& echo SSHD-IS'-UP'"

# Round two. Idempotent, because the loop below retries it: ssh-keygen refuses to
# overwrite an existing key, so generate one only if there is none.
#
# A key is the only way in that the shipped sshd_config allows — no account has a
# password, and PasswordAuthentication is off — so this doubles as the check that a key
# for root works under `PermitRootLogin prohibit-password`.
#
# BatchMode=yes so a failed key is an error rather than a password prompt this console
# would sit at until the deadline. StrictHostKeyChecking=no and a /dev/null known-hosts
# file because the host key was generated on this boot and nothing has seen it before.
#
# XDG_SESSION_ID is set by pam_systemd.so out of /etc/pam.d/sshd, and by nothing else in
# a non-interactive ssh command. An empty one means the session never reached logind,
# which is a broken PAM stack wearing a working login as a disguise.
#
# Tested for being non-empty rather than for looking like a session id, and that is not
# laziness: the ids here are `c1`, `c2`, `c3` and not `1`, `2`, `3`. logind derives a
# numeric id from the kernel's audit session id when there is one, and
# packages/kernel/build.sh turns CONFIG_AUDIT off (there is no audit userspace here), so
# it falls back to generating its own with a `c` prefix. Asserting a shape would be
# asserting the kernel config from the wrong end of the machine.
SSH_CHECK="install -d -m 700 /root/.ssh \
&& { [[ -f /root/.ssh/id_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/id_ed25519; } \
&& install -m 600 /root/.ssh/id_ed25519.pub /root/.ssh/authorized_keys \
&& [[ \$(ssh -n -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes root@127.0.0.1 id -un) == root ]] \
&& [[ -n \$(ssh -n -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes root@127.0.0.1 'echo \$XDG_SESSION_ID') ]] \
&& echo SSH-LOGIN'-OK'"

DIAGNOSE="systemctl --no-pager --full status sshd.service sshd-keygen.service
journalctl --no-pager -n 60 -u sshd.service -u sshd-keygen.service
ls -l /etc/ssh /var/lib/sshd; getent passwd sshd; ls -ld /usr/share/empty.sshd
ls -l /run/credentials/@system; cat /proc/cmdline"

command -v ssh >/dev/null || { echo "error: ssh not found (install an openssh client)" >&2; exit 1; }

qemu_setup
qemu_preflight
# Its own directory rather than output/ssh, so running this does not take the key out from
# under a guest tools/boot-qemu.sh is running. qemu_boot's cleanup deletes it.
SSH_DIR=$(mktemp -d)
qemu_ssh_setup
qemu_boot
console_login

echo ">> logged in, checking that sshd is up"

# Retried, for the same reason test/network.py retries its first round: sshd-keygen has an
# RSA key to generate at every boot and multi-user.target is reached without waiting
# for it. Re-asking costs nothing.
until grep -qaF "$DAEMON_MARKER" "$LOG"; do
    [ "$SECONDS" -lt "$deadline" ] || fail "sshd is not running, has no host key, or is not listening"
    grep -qaE "$QEMU_DIED" "$LOG" && fail "the guest died"
    kill -0 "$qemu_pid" 2>/dev/null || fail "qemu exited"
    console_send "$DAEMON_CHECK"
    sleep 5
done

echo ">> sshd OK: unit active, host key present, port 22 accepting connections"
echo ">> logging in over ssh, as the machine, to itself"

until grep -qaF "$LOGIN_MARKER" "$LOG"; do
    [ "$SECONDS" -lt "$deadline" ] || \
        fail "ssh could not log in (the daemon is up — see the console below)"
    grep -qaE "$QEMU_DIED" "$LOG" && fail "the guest died"
    kill -0 "$qemu_pid" 2>/dev/null || fail "qemu exited"
    console_send "$SSH_CHECK"
    sleep 5
done

echo ">> ssh OK: key authentication accepted for root, and logind registered the session"
echo ">> logging in from the host, through tools/ssh.sh and the forward on port $SSH_PORT"

# Round three. `id -un` and whether there is a session id, in one answer, so a single
# connection proves both. No marker trick needed: this is ssh's stdout, not a console that
# echoes what was typed, and the comparison is exact.
until out=$(SSH_DIR="$SSH_DIR" tools/ssh.sh --root -o ConnectTimeout=10 \
                'echo "$(id -un) ${XDG_SESSION_ID:+session}"' 2>&1) \
        && [ "$out" = "root session" ]; do
    [ "$SECONDS" -lt "$deadline" ] || \
        fail "tools/ssh.sh could not log in from the host (last answer: ${out:-nothing})"
    grep -qaE "$QEMU_DIED" "$LOG" && fail "the guest died"
    kill -0 "$qemu_pid" 2>/dev/null || fail "qemu exited"
    sleep 5
done

echo ">> ssh from the host OK: the ephemeral key was accepted through the boot credential"

# And tools/ssh.sh's default, `user`, through the copy of the same credential
# kaetzli-home.conf made in /run/ssh-authorized-keys. The runtime directory is the point: it is what rootless podman needs and
# what `su user` from a root session does not give it, so the answer has to name
# /run/user/1000, not merely the account.
out=$(SSH_DIR="$SSH_DIR" tools/ssh.sh -o ConnectTimeout=10 \
          'echo "$(id -un) ${XDG_RUNTIME_DIR:-none}"' 2>&1) || true
[ "$out" = "user /run/user/1000" ] || \
    fail "tools/ssh.sh did not log in as user with its own session (answer: ${out:-nothing})"
echo ">> ssh as user OK: its own session, with /run/user/1000"

if [ -n "${PREBUILT:-}" ]; then
    echo ">> running binaries built elsewhere, from $PREBUILT"
    SSH_DIR="$SSH_DIR" ./test/prebuilt.sh ssh "$PREBUILT" || \
        fail "a binary built by the host's toolchain does not run in the guest"
fi
