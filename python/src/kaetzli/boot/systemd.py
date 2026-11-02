"""test/systemd.py: boot with systemd as PID 1 and require that it comes up clean.

    ./test/systemd.py [rootfs.erofs] [bzImage]

Overrides, from the environment:
  TIMEOUT     seconds to wait    (default 300 — qemu falls back to TCG in CI, where
                                  there is no KVM, and that is slow)
  MEM/CPUS    guest size         (default 1024 / 2)
  LOG         console transcript (default output/systemd-test.log)
  LOGIN_USER  serial console account (default root; no password, see Guest.login)

`systemctl is-system-running` is the main assertion: it reports `degraded` if and only if
at least one unit failed, so it covers units nothing else here thinks to look at. --wait
blocks until startup has settled, which keeps this from racing a unit that is merely
slow; the deadline is what catches one that hangs. More checks ride along on the same
shell, because a login over a serial console is the expensive part and one more command
on it is free: systemd's Tainted property, writes to the read-only root, the journal's
errors, the image's sysctls, a credential through fw_cfg, and kaetzlifetch, the only
binary in usr/bin that anything in test/ runs for its own sake.

This is deliberately separate from test/boot.py and test/network.sh: test/boot.py proves
the kernel and the loader work with a raw shell as PID 1, test/network.sh proves
addressing and DNS, and this proves systemd itself is happy. Three failures that mean
three different things stay three tests; what they share is the harness, kaetzli.qemu.

In-guest commands are bash builtins and systemctl/journalctl only: keeping the handshake
free of other packages means a failure here means what it says. Every marker is split by
quotes in the command that prints it, so an echo of what was typed cannot match it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from kaetzli.boot import boot, run
from kaetzli.qemu import Guest

CHECK = "echo SYSTEMD'-STATE':$(systemctl is-system-running --wait)"

# The taint string is systemd's own summary of things it found wrong that no unit will
# ever fail over — an unmerged /usr/sbin, a /var/run that is a directory. Empty on a
# healthy system. Deliberately not `--value`: `systemctl show -p` prints nothing at all
# for a name it does not know, so a typo would read as "no taint" forever — which the
# first draft of this check did. The "Tainted=" prefix tells the two apart, and the
# brackets make an empty value something to match.
TAINT_CHECK = "echo SYSTEMD'-TAINT':[$(systemctl show -p Tainted)]"

# The root is EROFS and /etc cannot be written, so anything that still tries to at boot
# fails — and much of that failure is not a failed unit. PID 1's first-boot preset is the
# case that made this check: it could not write the links that enable networkd and
# resolved, logged one line, and the system came up `running` with no network. So count
# the boot's journal lines carrying EROFS's strerror. Builtins only; journalctl --grep
# would need pcre2, which systemd is built without.
ROFS_CHECK = (
    "n=0; while IFS= read -r l; do [[ $l == *'Read-only file system'* ]] && "
    '{ n=$((n+1)); echo "ROFS> $l"; }; done < <(journalctl -b --no-pager -o short); '
    "echo SYSTEMD'-ROFS':[$n]"
)

# A daemon can log an error and carry on, and the boot is still `running`. So the boot's
# journal at err and above has to be empty, but for what KNOWN_JOURNAL accepts (filtered
# on the host, by grep, since its lines are EREs). -q, so an empty journal prints nothing
# rather than "-- No entries --". From podman-machine-os, which requires it of crit.
ERRS_CHECK = (
    'n=0; while IFS= read -r l; do n=$((n+1)); echo "ERRS> $l"; '
    "done < <(journalctl -b -q -p err --no-pager -o short); echo SYSTEMD'-ERRS':[$n]"
)
KNOWN_JOURNAL = "test/known-journal-messages.txt"

# Sysctls image/files/etc/sysctl.d sets, read back: a file systemd-sysctl could not apply
# looks the same as one it did, but for one journal line. As /proc/sys paths, which is
# what a builtin can read.
SYSCTLS = [
    "kernel/kptr_restrict=1",
    "kernel/dmesg_restrict=1",
    "kernel/unprivileged_bpf_disabled=1",
    "kernel/yama/ptrace_scope=1",
    "dev/tty/legacy_tiocsti=0",
    "fs/protected_regular=2",
    "fs/protected_fifos=2",
    "fs/inotify/max_user_instances=8192",
    "vm/max_map_count=262144",
    "fs/aio-max-nr=1048576",
    "kernel/panic=10",
]
SYSCTL_CHECK = (
    "for k in "
    + " ".join(s.split("=", 1)[0] for s in SYSCTLS)
    + "; do echo \"SYSCTL> $k=$(< /proc/sys/$k)\"; done; echo SYSTEMD'-SYSCTL-DONE'"
)

# A credential through qemu's fw_cfg rather than the command line, on the pc profile:
# what libvirt and qemu offer for anything too big for the command line or too secret for
# /proc/cmdline, readable only through FW_CFG_SYSFS. Without it the credential is silently
# not there, so nothing else would notice it go.
FWCFG_CREDENTIAL = "kaetzli.fwcfg-probe=delivered"
FWCFG_CHECK = "echo SYSTEMD'-FWCFG':[$(< /run/credentials/@system/kaetzli.fwcfg-probe)]"

# --no-color: the transcript is read as text.
FETCH_RUN = "kaetzlifetch --no-color; echo KAETZLIFETCH'-DONE'"

DIAGNOSE = "systemctl --failed --no-pager --plain; journalctl -p err -b --no-pager -o short"


def _out(line: str = "") -> None:
    print(line, flush=True)


def _indented(lines: list[str]) -> None:
    for line in lines:
        _out(f"   {line}")


def _prefixed(text: str, prefix: str) -> list[str]:
    return [line[len(prefix) :] for line in text.split("\n") if line.startswith(prefix)]


def unknown_errors(lines: list[str], known_file: str = KNOWN_JOURNAL) -> list[str]:
    """The journal's error lines that the allowlist's EREs do not accept, by grep -vE,
    which reads them as the file says; an allowlist with no pattern accepts nothing."""
    known = [
        line
        for line in Path(known_file).read_text(encoding="utf-8").split("\n")
        if not re.match(r"^\s*(#|$)", line)
    ]
    if not known or not lines:
        return lines
    p = subprocess.run(
        ["grep", "-vE", *[a for k in known for a in ("-e", k)]],
        input="".join(f"{line}\n" for line in lines),
        capture_output=True,
        text=True,
    )
    if p.returncode > 1:
        raise RuntimeError(f"grep: {p.stderr.strip()}")
    return p.stdout.splitlines()


def check(guest: Guest, fwcfg: bool) -> None:
    _out(">> logged in, waiting for systemd to finish starting")

    # `running` is the only clean result: `degraded` means a unit failed, and
    # `maintenance`/`stopping` mean we should not have got a shell at all.
    guest.send(CHECK)
    if not guest.wait(r"SYSTEMD-STATE:([^\r\n]*)\r?\n", guest.deadline - guest.clock()):
        guest.fail("systemd never reported a final state")
    state = re.match(r"[a-z][a-z-]*", guest.group())
    result = state.group() if state else ""
    if result == "running":
        _out(">> systemd OK: system is running, no failed units")
    elif result == "degraded":
        guest.fail("system is degraded — at least one unit failed")
    else:
        guest.fail(f"unexpected system state: {result or '<none>'}")

    guest.send(TAINT_CHECK)
    if not guest.wait(r"SYSTEMD-TAINT:\[([^]]*)\]", 30):
        guest.fail("systemd never reported its taint string")
    taint = guest.group()
    if taint == "Tainted=":
        _out(">> systemd OK: taint string is empty")
    elif taint.startswith("Tainted="):
        guest.fail(
            f"systemd reports the system as tainted: {taint.removeprefix('Tainted=')} "
            "(systemd's src/core/taint.c says what each flag means)"
        )
    else:
        guest.fail(f"could not read systemd's Tainted property (got: {taint or '<nothing>'})")

    guest.mark()
    guest.send(ROFS_CHECK)
    if not guest.wait(r"SYSTEMD-ROFS:\[([0-9]*)\]", 60):
        guest.fail("could not count read-only filesystem errors in the journal")
    rofs = guest.group()
    if rofs == "0":
        _out(">> systemd OK: nothing tried to write to the read-only root")
    elif rofs:
        _indented(_prefixed(guest.before, "ROFS> "))
        guest.fail(
            f"{rofs} journal line(s) report a write to the read-only root (above) — see "
            "'First boot happens in build-rootfs.sh' in CLAUDE.md"
        )
    else:
        guest.fail("could not read the read-only filesystem error count (got: <nothing>)")

    guest.mark()
    guest.send(ERRS_CHECK)
    if not guest.wait(r"SYSTEMD-ERRS:\[", 60):
        guest.fail("could not read the journal's error messages")
    errs = unknown_errors(_prefixed(guest.before, "ERRS> "))
    if errs:
        _indented(errs)
        guest.fail(f"the journal has error messages (above) that {KNOWN_JOURNAL} does not accept")
    _out(">> systemd OK: no errors in the journal")

    guest.mark()
    guest.send(SYSCTL_CHECK)
    if not guest.wait("SYSTEMD-SYSCTL-DONE", 30):
        guest.fail("could not read the sysctls back")
    got = _prefixed(guest.before, "SYSCTL> ")
    for want in SYSCTLS:
        if want not in got:
            key, value = want.split("=", 1)
            # grep "^key=" | cut -d= -f2: every line it finds, the second field of each.
            seen = "\n".join(g.split("=")[1] for g in got if g.startswith(f"{key}="))
            guest.fail(
                f"sysctl {key} is {seen} in the guest, image/files/etc/sysctl.d sets {value}"
            )
    _out(">> systemd OK: the image's sysctls are applied")

    if fwcfg:
        guest.send(FWCFG_CHECK)
        if not guest.wait(r"SYSTEMD-FWCFG:\[([^]]*)\]", 30):
            guest.fail("could not read the fw_cfg credential")
        if guest.group() != "delivered":
            guest.fail(
                f"a credential passed through fw_cfg did not arrive (got: [{guest.group()}])"
                " — is CONFIG_FW_CFG_SYSFS in the kernel (guest.config)?"
            )
        _out(">> systemd OK: a credential arrived through fw_cfg")

    # Partly for the pleasure of seeing it in a CI log, and partly because kaetzlifetch is
    # a fair canary: it links nothing but glibc and reads /etc/os-release, /proc and /sys,
    # so "it printed its own OS name" means the loader, the libc and the shipped /etc all
    # did their jobs. A hard failure: a binary in the image that cannot run is what this
    # directory exists to catch.
    guest.mark()
    guest.send(FETCH_RUN)
    if not guest.wait("KAETZLIFETCH-DONE", 30):
        guest.fail("kaetzlifetch did not run (is /usr/bin/kaetzlifetch in the image?)")
    said = guest.before
    _out(">> kaetzlifetch says:")
    _indented(said.removesuffix("\n").split("\n") if said else [])

    # The OS field exists only if /etc/os-release was found and parsed: kaetzlifetch omits
    # a field it cannot fill. The field's name, not its value, so a rename is not a failure.
    if "OS: " not in said:
        guest.fail("kaetzlifetch ran but printed no OS field — /etc/os-release unreadable?")
    # No /var disk and a verity root: the setup fields have one right answer each, and
    # they are what a user goes by to judge whether what they install survives a reboot.
    for want in ("Root: erofs, read-only, dm-verity", "State: in memory", "Image: "):
        if want not in said:
            guest.fail(f"kaetzlifetch did not print '{want}'")
    _out(">> kaetzlifetch OK")


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="output/systemd-test.log")
    user = os.environ.get("LOGIN_USER") or "root"

    def test() -> None:
        b.machine.setup()
        if b.machine.profile == "pc":
            b.machine.fwcfg_credentials = [FWCFG_CREDENTIAL]
        b.machine.preflight()
        with b.guest("systemd", diagnose=DIAGNOSE) as guest:
            guest.login(user)
            check(guest, fwcfg=bool(b.machine.fwcfg_credentials))

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
