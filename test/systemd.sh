#!/usr/bin/env bash
# Boot the image with systemd as PID 1 and require that it comes up clean — no failed
# units — so a package that ships a unit it cannot actually run fails CI instead of
# leaving every boot `degraded` for someone to notice by hand.
#
#     ./test/systemd.sh [rootfs.erofs] [bzImage]
#
# Overrides:
#   TIMEOUT    seconds to wait   (default 300 — qemu falls back to TCG in CI, where
#                                there is no KVM, and that is slow)
#   MEM/CPUS   guest size        (default 1024 / 2)
#   LOG        console transcript (default systemd-test.log)
#   LOGIN_USER                 serial console account (default root; no password, see console_login)
#
# `systemctl is-system-running` is the main assertion: it reports `degraded` if and
# only if at least one unit failed, so it covers units nothing else here thinks to look
# at. --wait blocks until startup has actually settled, which is what keeps this from
# racing a unit that is merely slow; the deadline below is what catches one that hangs.
# Two more ride along on the same shell, because a login over a serial console is the
# expensive part and running one more command on it is free: systemd's Tainted property,
# and kaetzlifetch, which is the only binary in usr/bin that anything in test/ ever runs
# for its own sake.
#
# This is deliberately separate from test/boot.py and test/network.sh rather than folded
# into either: test/boot.py proves the kernel and the loader work with a raw shell as
# PID 1, test/network.sh proves addressing and DNS, and this proves systemd itself is
# happy. Three failures that mean three different things stay three different tests —
# what they share is the plumbing, which is test/qemu-lib.sh.
set -euo pipefail

cd "$(dirname "$0")/.."
source test/qemu-lib.sh

ROOTFS="${1:-${ROOTFS:-output/rootfs.erofs}}"
KERNEL="${2:-${KERNEL:-rootfs/boot/bzImage}}"
INIT="${INIT:-/usr/lib/systemd/systemd}"
LOG="${LOG:-output/systemd-test.log}"
TEST_NAME=systemd

# Same trick as the READY marker in test/qemu-lib.sh: the guest echoes back what is typed
# at the console until `stty -echo` takes effect, so a marker must not appear verbatim in
# the command that produces it. The guest's shell strips the quotes; the patterns below
# are the joined string.
#
# In-guest commands are bash builtins and systemctl only. grep, sed and awk are in the
# image now, but [[ ]] and command substitution already cover this, and keeping the
# handshake free of other packages means a failure here means what it says.
STATE="SYSTEMD-STATE:"
TAINT="SYSTEMD-TAINT:"
FETCH="KAETZLIFETCH-DONE"
CHECK="echo SYSTEMD'-STATE':\$(systemctl is-system-running --wait)"
# --no-color because this transcript is read as text, in a CI log or output/*.log.
FETCH_RUN="kaetzlifetch --no-color; echo KAETZLIFETCH'-DONE'"
# The taint string is systemd's own summary of things it found wrong with the system that
# no unit will ever fail over — an unmerged /usr/sbin, a /var/run that is a directory. It
# is a colon-separated word list, empty on a healthy system, so it costs one more command
# and catches a whole class of image-assembly mistakes that otherwise only turn up when
# somebody happens to read `systemctl status` on a booted guest.
#
# Deliberately not `--value`: the property is "Tainted", and `systemctl show -p` prints
# *nothing at all* for a name it does not know, so a typo here would produce an empty
# string and silently pass forever — which is exactly what the first draft of this check
# did. Keeping the "Tainted=" prefix in the output lets it tell "no taint" apart from
# "never asked the right question".
TAINT_CHECK="echo SYSTEMD'-TAINT':[\$(systemctl show -p Tainted)]"
# The root filesystem is EROFS and /etc cannot be written, so anything that still tries to
# at boot fails — and a lot of that failure is not a failed unit. PID 1's first-boot preset
# is the case that made this check exist: it could not write the symlinks that enable
# networkd and resolved, logged one line, and the system came up `running` with no
# network. So count the boot's journal lines that carry EROFS's strerror, which is what
# every one of those writes says however it is reported. Builtins only, for the reason
# above; journalctl --grep would need pcre2, which systemd is built without.
ROFS="SYSTEMD-ROFS:"
ROFS_CHECK="n=0; while IFS= read -r l; do [[ \$l == *'Read-only file system'* ]] && { n=\$((n+1)); echo \"ROFS> \$l\"; }; done < <(journalctl -b --no-pager -o short); echo SYSTEMD'-ROFS':[\$n]"
# A daemon can log an error and carry on, and the boot is still `running`. So the boot's
# journal at priority err and above has to be empty, but for what
# test/known-journal-messages.txt accepts (filtered here on the host, where grep is). -q,
# so an empty journal prints nothing rather than "-- No entries --". From
# podman-machine-os, whose image test requires the same of crit; err is stricter, and
# was empty when it was turned on.
ERRS="SYSTEMD-ERRS:"
ERRS_CHECK="n=0; while IFS= read -r l; do n=\$((n+1)); echo \"ERRS> \$l\"; done < <(journalctl -b -q -p err --no-pager -o short); echo SYSTEMD'-ERRS':[\$n]"
KNOWN_JOURNAL=test/known-journal-messages.txt
# Sysctls image/files/etc/sysctl.d sets, read back: a file systemd-sysctl could not apply
# looks the same as one it did, but for one journal line. As /proc/sys paths, which is
# what a builtin can read.
SYSCTLS="kernel/kptr_restrict=1 kernel/dmesg_restrict=1 kernel/unprivileged_bpf_disabled=1
kernel/yama/ptrace_scope=1 dev/tty/legacy_tiocsti=0 fs/protected_regular=2 fs/protected_fifos=2
fs/inotify/max_user_instances=8192 vm/max_map_count=262144 fs/aio-max-nr=1048576 kernel/panic=10"
SYSCTL="SYSTEMD-SYSCTL-DONE"
sysctl_paths=$(printf '%s\n' $SYSCTLS | cut -d= -f1 | tr '\n' ' ')   # one line: it is typed at a shell
SYSCTL_CHECK="for k in $sysctl_paths; do echo \"SYSCTL> \$k=\$(< /proc/sys/\$k)\"; done; echo SYSTEMD'-SYSCTL-DONE'"
DIAGNOSE="systemctl --failed --no-pager --plain; journalctl -p err -b --no-pager -o short"
# A credential through qemu's fw_cfg rather than the command line, on the pc profile: what
# libvirt and qemu offer for anything too big for the command line or too secret for
# /proc/cmdline, readable only through FW_CFG_SYSFS. Without it the credential is silently
# not there, so nothing else would notice it go. $(< file) is a builtin; the value is not
# in the command typed, so the echo cannot match.
FWCFG="SYSTEMD-FWCFG:"
FWCFG_CHECK="echo SYSTEMD'-FWCFG':[\$(< /run/credentials/@system/kaetzli.fwcfg-probe)]"

qemu_setup
[ "$PROFILE" != pc ] || QEMU_FWCFG_CREDENTIALS="kaetzli.fwcfg-probe=delivered"
qemu_preflight
qemu_boot
console_login

echo ">> logged in, waiting for systemd to finish starting"

console_send "$CHECK"
await "$STATE" "$((deadline - SECONDS))" || fail "systemd never reported a final state"

# One line, so the last match is the answer. `running` is the only clean result:
# `degraded` means a unit failed, and `maintenance`/`stopping` mean we should not have
# got a shell at all.
result=$(tr -d '\r' < "$LOG" | sed -n "s/.*${STATE}\\([a-z][a-z-]*\\).*/\\1/p" | tail -1)

case "$result" in
    running)
        echo ">> systemd OK: system is running, no failed units"
        ;;
    degraded)
        fail "system is degraded — at least one unit failed"
        ;;
    *)
        fail "unexpected system state: ${result:-<none>}"
        ;;
esac

# Bracketed, so an empty taint string is still something to match on rather than the
# absence of output — which would be indistinguishable from the command never running.
console_send "$TAINT_CHECK"
await "$TAINT" 30 || fail "systemd never reported its taint string"

taint=$(tr -d '\r' < "$LOG" | sed -n "s/.*${TAINT}\\[\\([^]]*\\)\\].*/\\1/p" | tail -1)

case "$taint" in
    "Tainted=")
        echo ">> systemd OK: taint string is empty"
        ;;
    "Tainted="*)
        fail "systemd reports the system as tainted: ${taint#Tainted=} (systemd's src/core/taint.c says what each flag means)"
        ;;
    *)
        fail "could not read systemd's Tainted property (got: ${taint:-<nothing>})"
        ;;
esac

console_send "$ROFS_CHECK"
await "$ROFS" 60 || fail "could not count read-only filesystem errors in the journal"

rofs=$(tr -d '\r' < "$LOG" | sed -n "s/.*${ROFS}\\[\\([0-9]*\\)\\].*/\\1/p" | tail -1)

case "$rofs" in
    0)
        echo ">> systemd OK: nothing tried to write to the read-only root"
        ;;
    [0-9]*)
        tr -d '\r' < "$LOG" | grep '^ROFS> ' | sed 's/^ROFS> /   /' || true
        fail "$rofs journal line(s) report a write to the read-only root (above) — see 'First boot happens in build-rootfs.sh' in CLAUDE.md"
        ;;
    *)
        fail "could not read the read-only filesystem error count (got: ${rofs:-<nothing>})"
        ;;
esac

mark=$(console_mark)
console_send "$ERRS_CHECK"
await "$ERRS" 60 "$mark" || fail "could not read the journal's error messages"
# The allowlist without its comments and blank lines; an empty one matches nothing.
known=$(grep -vE '^[[:space:]]*(#|$)' "$KNOWN_JOURNAL" || true)
errs=$(tail -c "+$((mark + 1))" "$LOG" | tr -d '\r' | sed -n 's/^ERRS> //p' |
       { if [ -n "$known" ]; then grep -vE -e "$known"; else cat; fi; } || true)
if [ -n "$errs" ]; then
    printf '%s\n' "$errs" | sed 's/^/   /'
    fail "the journal has error messages (above) that $KNOWN_JOURNAL does not accept"
fi
echo ">> systemd OK: no errors in the journal"

mark=$(console_mark)
console_send "$SYSCTL_CHECK"
await "$SYSCTL" 30 "$mark" || fail "could not read the sysctls back"
got=$(tail -c "+$((mark + 1))" "$LOG" | tr -d '\r' | sed -n 's/^SYSCTL> //p')
for want in $SYSCTLS; do
    grep -qxF "$want" <<<"$got" ||
        fail "sysctl ${want%=*} is $(grep "^${want%=*}=" <<<"$got" | cut -d= -f2) in the guest, image/files/etc/sysctl.d sets ${want#*=}"
done
echo ">> systemd OK: the image's sysctls are applied"

if [ -n "${QEMU_FWCFG_CREDENTIALS:-}" ]; then
    console_send "$FWCFG_CHECK"
    await "$FWCFG" 30 || fail "could not read the fw_cfg credential"
    fwcfg=$(tr -d '\r' < "$LOG" | sed -n "s/.*${FWCFG}\\[\\([^]]*\\)\\].*/\\1/p" | tail -1)
    [ "$fwcfg" = delivered ] ||
        fail "a credential passed through fw_cfg did not arrive (got: [${fwcfg}]) — is CONFIG_FW_CFG_SYSFS in the kernel (guest.config)?"
    echo ">> systemd OK: a credential arrived through fw_cfg"
fi

# Partly for the pleasure of seeing it in a CI log, and partly because this is the only
# place anything in test/ runs a binary out of usr/bin that is not already on systemd's
# or login's critical path. It rides along on the shell this test already has rather
# than costing a fifth qemu boot, and kaetzlifetch is a fair canary: it links nothing but
# glibc and reads /etc/os-release, /proc and /sys, so "it printed its own OS name" means
# the loader, the libc and the shipped /etc all did their jobs.
#
# It is a hard failure rather than a nicety. A binary that is in the image and cannot run
# is exactly the class of bug the rest of this directory exists to catch.
mark=$(console_mark)
console_send "$FETCH_RUN"
await "$FETCH" 30 "$mark" || fail "kaetzlifetch did not run (is /usr/bin/kaetzlifetch in the image?)"

# Everything the guest emitted after the command was typed, minus the end marker itself.
echo ">> kaetzlifetch says:"
tail -c "+$((mark + 1))" "$LOG" | tr -d '\r' | sed -e "/$FETCH/,\$d" -e 's/^/   /'

# The OS field exists only if /etc/os-release was found and parsed — kaetzlifetch omits a
# field it cannot fill rather than printing it empty. Matching the field name rather than
# its value keeps this from breaking the day the name in image/files/etc/os-release
# changes.
if ! tail -c "+$((mark + 1))" "$LOG" | grep -qa 'OS: '; then
    fail "kaetzlifetch ran but printed no OS field — /etc/os-release unreadable?"
fi
# This boot has no /var disk and a verity root, so the setup fields have one right
# answer each. They are read from the mount table and the command line, and are the
# summary a user goes by when deciding whether what they install will survive a reboot.
for want in 'Root: erofs, read-only, dm-verity' 'State: in memory' 'Image: '; do
    tail -c "+$((mark + 1))" "$LOG" | grep -qaF "$want" ||
        fail "kaetzlifetch did not print '$want'"
done
echo ">> kaetzlifetch OK"
