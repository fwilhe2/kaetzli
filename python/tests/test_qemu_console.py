"""kaetzli.qemu.console and the boot smoke test, against a fake qemu.

fixtures/qemu/fake_guest.py is a console that behaves as a scenario says: a shell, a
deaf console, a panic, a getty that loses what is typed at first. What the Python must
reproduce of the bash harness (statuses, messages, the console check) was checked by
running both on a real image; the messages are pinned here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from kaetzli.boot import smoke
from kaetzli.qemu import console
from kaetzli.qemu.console import Failed, Guest

FAKE = Path(__file__).parent / "fixtures" / "qemu" / "fake_guest.py"


@pytest.fixture
def guest(tmp_path):
    def make(scenario: str, timeout: float = 5, **kwargs) -> Guest:
        return Guest(
            sys.executable,
            [str(FAKE), scenario],
            tmp_path / "out" / "console.log",
            timeout,
            settle=0,
            **kwargs,
        )

    return make


class TestSmoke:
    def test_a_shell_answers(self, guest, capsys):
        with guest("shell") as g:
            assert smoke.smoke(g, "bash-[0-9]", retry=0.2) == "ok"
        assert b"BOOT-SMOKE-OK" in g.log.read_bytes()

    def test_a_deaf_console_times_out_after_the_prompt(self, guest):
        with pytest.raises(Failed), guest("deaf", timeout=1.5) as g:
            assert smoke.smoke(g, "bash-[0-9]", retry=0.2) == "timeout"
            raise Failed("")

    def test_no_prompt_is_said_apart(self, guest):
        with pytest.raises(Failed), guest("silent", timeout=1) as g:
            assert smoke.smoke(g, "bash-[0-9]") == "timeout, no shell prompt"
            raise Failed("")

    @pytest.mark.parametrize(
        ("scenario", "status"),
        [("panic", "died"), ("exit", "exited"), ("panic-exit", "died")],
    )
    def test_a_guest_that_goes(self, guest, scenario, status):
        with pytest.raises(Failed), guest(scenario) as g:
            assert smoke.smoke(g, "never") == status
            raise Failed("")

    def test_the_lines_after_the_marker_are_greps(self, tmp_path):
        log = b"a\r\nBOOT-SMOKE-OK\r\nb\r\nc\r\nd\r\nBOOT-SMOKE-OK x\r\ne\r\nBOOT-SMOKE-OK\r\nf"
        (tmp_path / "log").write_bytes(log)
        grep = subprocess.run(
            "grep -F -A2 BOOT-SMOKE-OK log | sed 's/^/   /' | head -5",
            shell=True, cwd=tmp_path, capture_output=True, check=True,
        ).stdout  # fmt: skip
        assert smoke._after_marker(log) == grep


class TestWait:
    def test_a_match_is_consumed(self, guest):
        """Each wait sees only what came after the last match: the marks rule."""
        with guest("twice") as g:
            assert g.wait("ready", 2)
            assert g.wait("ready", 2)
            assert not g.wait("ready", 0.5)

    def test_mark_forgets_what_is_there(self, guest):
        with guest("twice") as g:
            assert g.wait("Linux version", 2)
            g.drain(0.5)
            g.mark()
            assert not g.wait("ready", 0.5)

    def test_the_deadline_fails(self, guest, capsys):
        with pytest.raises(Failed), guest("silent", timeout=0.5) as g:
            g.wait("never", 5)
        err = capsys.readouterr().err
        assert err.startswith(">> boot test FAILED: timed out waiting for: never\n")
        assert ">> last 80 lines of the console:\n" in err
        assert "Run /bin/bash as init process" in err

    @pytest.mark.parametrize(
        ("scenario", "message"),
        [("panic", "the guest died"), ("exit", "qemu exited")],
    )
    def test_a_guest_that_goes_fails(self, guest, capsys, scenario, message):
        with pytest.raises(Failed), guest(scenario, name="systemd") as g:
            g.wait("never", 3)
        assert capsys.readouterr().err.startswith(f">> systemd test FAILED: {message}\n")

    def test_diagnose_is_typed_and_its_answer_shown(self, guest, capsys, monkeypatch):
        monkeypatch.setattr(Guest, "drain", _short(Guest.drain))
        with pytest.raises(Failed), guest("shell", diagnose="diagnose") as g:
            g.wait("bash-5", 2)
            g.fail("on purpose")
        assert "DIAGNOSIS-HERE" in capsys.readouterr().err


def _short(drain):
    def short(self, seconds, until_eof=False):
        return drain(self, min(seconds, 1), until_eof)

    return short


class TestLogin:
    def test_retried_until_a_shell(self, guest, capsys, monkeypatch):
        typed = []
        send = Guest.send
        monkeypatch.setattr(Guest, "send", lambda g, line: (typed.append(line), send(g, line)))
        with guest("getty", probe_wait=0.5) as g:
            g.login("root")
            g.send("echo BOOT-SMOKE'-OK'")
            assert g.wait("BOOT-SMOKE-OK", 2)
        assert capsys.readouterr().out == ">> got a login prompt, logging in as root\n"
        # agetty lost the first attempt, so the name was typed again.
        assert typed.count("root") > 1
        assert g.log.read_bytes().count(b"kaetzli login: ") == 1
        assert b"Last login" in g.log.read_bytes()

    def test_rejected(self, guest, capsys):
        with pytest.raises(Failed), guest("rejected", timeout=1.5, probe_wait=0.3) as g:
            g.login("root")
        err = capsys.readouterr().err
        # As in bash, the deadline mostly passes inside the wait for the probe's answer.
        assert err.startswith(">> boot test FAILED: timed out waiting for: SHELL-IS-UP\n")

    def test_no_prompt(self, guest, capsys):
        with pytest.raises(Failed), guest("silent", timeout=0.5) as g:
            g.login()
        assert capsys.readouterr().err.startswith(">> boot test FAILED: no login prompt\n")


class TestConsoleCheck:
    def test_bad_lines_fail_a_passing_test(self, guest, capsys):
        with pytest.raises(Failed), guest("oops", name="systemd") as g:
            g.wait("bash-5", 2)
        assert capsys.readouterr().err == (
            ">> systemd test FAILED: the console shows a kernel warning, a crash or an "
            "emergency shell (CONSOLE_BAD in python/src/kaetzli/qemu/console.py):\n"
            "   [    1.000000] WARNING: CPU: 0 PID: 1 at mm/x.c:1 f+0x1/0x2\n"
            "   [    1.000001] Call Trace:\n"
            f">> the whole console is in {g.log}\n"
        )

    def test_exempt_lines_pass(self, guest):
        with guest("oops", exempt="WARNING: CPU|Call Trace") as g:
            g.wait("bash-5", 2)

    def test_a_failing_test_reports_its_own_reason(self, guest, capsys):
        with pytest.raises(RuntimeError), guest("oops") as g:
            g.wait("bash-5", 2)
            raise RuntimeError("its own")
        assert "CONSOLE_BAD" not in capsys.readouterr().err

    def test_the_whole_log_is_checked(self, guest):
        """Not only what is left after the last match."""
        with pytest.raises(Failed), guest("oops") as g:
            g.wait("bash-5", 2)
            g.mark()


class TestStop:
    def test_qemu_is_gone_and_cleanup_done(self, guest, tmp_path):
        keys = tmp_path / "ssh"
        keys.mkdir()
        with guest("silent", cleanup=[keys]) as g:
            proc = g.child.proc
        assert proc.poll() is not None
        assert not keys.exists()


def test_tail_is_tails(tmp_path):
    for data in (b"", b"a", b"a\n", b"a\nb", b"\n\n", b"".join(b"%d\n" % i for i in range(200))):
        (tmp_path / "f").write_bytes(data)
        want = subprocess.run(["tail", "-80", tmp_path / "f"], capture_output=True).stdout
        assert console._tail(data, 80) == want, data
