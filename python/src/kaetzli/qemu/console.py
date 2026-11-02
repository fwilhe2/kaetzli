"""A running guest and its console: test/qemu-lib.sh's qemu_boot, console_send, await,
console_login, fail and console_check, on pexpect.

qemu runs with its stdin and stdout on pipes, as the bash harness's fifo had it, and
everything it prints goes to the log. The difference is in waiting: a wait sees only
what came after the previous match, or after `mark()`, never the whole transcript. That
is the rule the bash harness kept by hand with console_mark (CLAUDE.md: matching the
whole transcript once typed the password into the username prompt); here it cannot be
forgotten.

    with Guest(machine.qemu, machine.argv(), log, timeout, name="systemd") as g:
        g.login("root")
        g.send("systemctl is-system-running")
        g.wait("running", 30) or g.fail("not running")

Leaving the block stops qemu; a block left without an exception also holds the console
to CONSOLE_BAD, which raises Failed when it finds something.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import sys
import time
from pathlib import Path

import pexpect
from pexpect.popen_spawn import PopenSpawn

# What a guest looks like on its way out.
QEMU_DIED = "Kernel panic|Attempted to kill init|Requesting system (poweroff|reboot)"

# What must never appear on a console, whatever the test is about: a guest that logs one
# of these usually keeps running, so nothing else would fail. Matched over the whole log
# when a boot ends without the test having failed; a test that provokes one on purpose
# names the lines it expects in `exempt`. From Flatcar's kola. The same list as
# test/qemu-lib.sh's, which a test holds this to.
CONSOLE_BAD = (
    "Oops:|BUG: |WARNING: CPU: [0-9]+ PID: [0-9]+ at |general protection fault|"
    "Call Trace:|segfault at |=11/SEGV|[Cc]ore dump|EXT4-fs error|Remounting filesystem read-only|"
    "device-mapper: verity: .* is corrupted|You are in emergency mode|Starting Emergency Shell"
)

# The marker is split by quotes so the echo of what we typed cannot match it.
READY = "SHELL-IS-UP"
PROBE = "echo SHELL-IS'-UP'"
# Echo off, prompt and colours off: the log is read as text.
QUIET = "stty -echo; PS1=; export SYSTEMD_COLORS=0"


_END = re.compile(rb"\Z")

# How long fail() gives the guest to answer `diagnose`, and how long login() lets agetty
# take the username before typing at the shell.
DIAGNOSE_WAIT = 5
SETTLE = 3


class Failed(Exception):
    """The test failed; what it says has been printed already. Exit 1."""


def _re(pattern: str) -> re.Pattern[bytes]:
    # ERE as grep reads it: per line, so ^ and $ are a line's ends.
    return re.compile(pattern.encode(), re.MULTILINE)


class Guest:
    def __init__(
        self,
        qemu: str,
        argv: list[str],
        log: str | Path,
        timeout: float,
        *,
        name: str = "boot",
        diagnose: str = "",
        exempt: str = "",
        cleanup: list[str | Path] | None = None,
        settle: float | None = None,
        probe_wait: float = 10,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.cmd = [qemu, *argv]
        self.log = Path(log)
        self.timeout = timeout
        self.name = name
        self.diagnose = diagnose
        self.exempt = exempt
        self.cleanup = cleanup or []
        self.settle = SETTLE if settle is None else settle
        self.probe_wait = probe_wait
        self.clock = clock
        self.sleep = sleep
        self.child: PopenSpawn | None = None
        self.deadline = 0.0
        self._died = _re(QEMU_DIED)
        # The last successful wait's match, and what the console said between the match
        # or mark before it and this one, carriage returns removed.
        self.match: re.Match[bytes] | None = None
        self.before = ""

    # qemu_boot

    def start(self) -> Guest:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self._logf = open(self.log, "wb", buffering=0)  # noqa: SIM115 (closed in stop)
        self.child = PopenSpawn(self.cmd, timeout=None)
        self.child.logfile_read = self._logf
        self.deadline = self.clock() + self.timeout
        return self

    def __enter__(self) -> Guest:
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.stop()
        if exc_type is None:
            self.console_check()
        return False

    def stop(self) -> None:
        if self.child is not None:
            self.drain(0)
            with contextlib.suppress(OSError):
                self.child.sendeof()
            if self.child.proc.poll() is None:
                self.child.proc.terminate()
            self.child.proc.wait()
            self.drain(5, until_eof=True)
            self.child = None
            self._logf.close()
        for path in self.cleanup:
            shutil.rmtree(path, ignore_errors=True)

    # console_send, console_mark

    def send(self, line: str) -> None:
        self.child.sendline(line)

    def mark(self, settle: float = 0.25) -> None:
        """Forget what is on the console so far: the next wait sees only what follows.
        It settles first, so the tail of the last command's output (a prompt, a terminal
        mode switch) is forgotten too, rather than read as the next one's."""
        self.drain(settle)
        # A match at the very end consumes it all. Not `buffer = b""`: pexpect keeps a
        # second copy of what is unmatched and would make the buffer again from it.
        self.child.expect(_END, timeout=0)

    def seen(self, pattern: str) -> bool:
        """Whether what arrived since the last match or mark matches, without waiting."""
        self.drain(0)
        return _re(pattern).search(self.child.buffer) is not None

    def drain(self, seconds: float, until_eof: bool = False) -> None:
        """Read what qemu has written into the buffer and the log, matching nothing."""
        patterns = [pexpect.EOF] if until_eof else [pexpect.EOF, pexpect.TIMEOUT]
        with contextlib.suppress(pexpect.TIMEOUT, pexpect.EOF):
            self.child.expect(patterns, timeout=seconds)

    # await

    def watch(self, pattern: str, seconds: float) -> str:
        """Wait up to `seconds` for `pattern`: "match", "died" (QEMU_DIED on the console),
        "exited" (qemu did) or "timeout". A match consumes the console up to its end."""
        i = self.child.expect(
            [_re(pattern), self._died, pexpect.EOF, pexpect.TIMEOUT], timeout=max(seconds, 0)
        )
        if i == 0:
            self.match = self.child.match
            self.before = self.child.before.decode(errors="replace").replace("\r", "")
            return "match"
        if i == 1:
            return "died"
        if i == 2:
            # The panic may have been consumed by an earlier match; the log has it all.
            return "died" if self._died.search(self.log.read_bytes()) else "exited"
        return "timeout"

    def group(self, n: int = 1) -> str:
        """A group of the last successful wait's match, as text."""
        return self.match.group(n).decode(errors="replace").replace("\r", "")

    def wait(self, pattern: str, seconds: float) -> bool:
        """True when `pattern` shows up within `seconds`, False when it does not (so the
        caller can retry); fails the test when the guest dies or the overall deadline
        passes."""
        until = self.clock() + seconds
        while True:
            now = self.clock()
            if now >= until:
                return False
            if now >= self.deadline:
                self.fail(f"timed out waiting for: {pattern}")
            status = self.watch(pattern, min(until, self.deadline) - now)
            if status == "match":
                return True
            if status == "died":
                self.fail("the guest died")
            if status == "exited":
                self.fail("qemu exited")

    # console_login

    def login(self, user: str = "root") -> None:
        """Log in at the serial getty and leave a quiet shell. The username is the whole
        login (the login.noauth credential). Retried, since agetty loses what is typed
        while it sets the line up; only output after the username satisfies the wait."""
        # Until the deadline exactly, so that running out is "no login prompt".
        if not self.wait("login:", self.deadline - self.clock()):
            self.fail("no login prompt")
        print(f">> got a login prompt, logging in as {user}", flush=True)
        while not self.seen(READY):
            if self.clock() >= self.deadline:
                self.fail("could not get a shell (login rejected?)")
            self.mark()
            self.send(user)
            self.sleep(self.settle)
            self.send(QUIET)
            self.send(PROBE)
            if self.wait(READY, self.probe_wait):
                return

    # fail, console_check

    def fail(self, message: str) -> None:
        """Say why, type `diagnose` at the guest so its answer is in the transcript, show
        the end of the console, and raise Failed."""
        print(f">> {self.name} test FAILED: {message}", file=sys.stderr, flush=True)
        if self.diagnose and self.child is not None:
            with contextlib.suppress(OSError):
                self.send(self.diagnose)
            self.drain(DIAGNOSE_WAIT)
        elif self.child is not None:
            self.drain(0)
        print(">> last 80 lines of the console:", file=sys.stderr, flush=True)
        sys.stderr.buffer.write(_tail(self.log.read_bytes(), 80))
        sys.stderr.flush()
        raise Failed(message)

    def console_check(self) -> None:
        """The CONSOLE_BAD lines in the log, less `exempt`'s: raises Failed naming them."""
        bad_re = _re(CONSOLE_BAD)
        exempt_re = _re(self.exempt) if self.exempt else None
        lines = self.log.read_bytes().replace(b"\r", b"").split(b"\n")
        bad = [
            line
            for line in lines
            if bad_re.search(line) and not (exempt_re and exempt_re.search(line))
        ]
        if not bad:
            return
        print(
            f">> {self.name} test FAILED: the console shows a kernel warning, a crash or an "
            "emergency shell (CONSOLE_BAD in python/src/kaetzli/qemu/console.py):",
            file=sys.stderr,
        )
        sys.stderr.flush()
        sys.stderr.buffer.write(b"".join(b"   " + line + b"\n" for line in bad[:10]))
        print(f">> the whole console is in {self.log}", file=sys.stderr, flush=True)
        raise Failed("CONSOLE_BAD")


def _tail(data: bytes, n: int) -> bytes:
    """tail -n: the last n lines, a final line without its newline counted as one."""
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        return b"\n".join(lines[:-1][-n:]) + b"\n" if len(lines) > 1 else b""
    return b"\n".join(lines[-n:])
