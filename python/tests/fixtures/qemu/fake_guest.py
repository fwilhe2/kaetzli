"""A stand-in for qemu in python/tests/test_qemu_console.py: a console on stdin and
stdout that behaves as the scenario in argv[1] says. Lines end in CRLF, as a serial
console's do."""

import json
import sys
import time
from pathlib import Path


def out(text: str) -> None:
    sys.stdout.write(text.replace("\n", "\r\n"))
    sys.stdout.flush()


def lines():
    for line in sys.stdin:
        yield line.rstrip("\n")


def shell(prompt: str = "bash-5.3# ") -> None:
    out(prompt)
    for line in lines():
        out(line + "\n")  # the console echoes what is typed
        if "echo BOOT-SMOKE'-OK'" in line:
            out("Linux 7.3.1 x86_64\nBOOT-SMOKE-OK\n")
        elif line == "echo SHELL-IS'-UP'":
            out("SHELL-IS-UP\n")
        elif line == "diagnose":
            out("DIAGNOSIS-HERE\n")
        out(prompt)


def systemd(responses: list[list[str]]) -> None:
    """A getty, then a shell that answers each command with the first response whose
    needle is in it, and echoes what is typed until `stty -echo`."""
    out("Welcome to Kaetzli!\n\nkaetzli login: ")
    next(lines())
    echo = True
    for line in lines():
        if echo:
            out(line + "\n")
        if line.startswith("stty -echo"):
            echo = False
        elif line == "echo SHELL-IS'-UP'":
            out("SHELL-IS-UP\n")
        else:
            for needle, answer in responses:
                if needle in line:
                    out(answer)
                    break


def main(scenario: str) -> None:
    out("[    0.000000] Linux version 7.3.1\n[    0.100000] Run /bin/bash as init process\n")
    if scenario == "shell":
        shell()
    elif scenario == "deaf":
        out("bash-5.3# ")
        for line in lines():
            out(line + "\n")
    elif scenario == "silent":
        time.sleep(60)
    elif scenario == "panic":
        out("[    0.200000] Kernel panic - not syncing: Attempted to kill init!\n")
        time.sleep(60)
    elif scenario == "exit":
        sys.exit(0)
    elif scenario == "panic-exit":
        out("[    0.200000] Kernel panic - not syncing: VFS\n")
        time.sleep(0.5)
        sys.exit(0)
    elif scenario == "twice":
        out("one: ready\none: ready\n")
        time.sleep(60)
    elif scenario == "getty":
        # agetty loses what is typed while it sets the line up: here, the first second.
        out("\nkaetzli login: ")
        start = time.monotonic()
        for _line in lines():
            if time.monotonic() - start > 1:
                break
        out("Last login: never\n")
        shell(prompt="")
    elif scenario == "rejected":
        out("\nkaetzli login: ")
        for _ in lines():
            out("\nLogin incorrect\nkaetzli login: ")
    elif scenario == "oops":
        out(
            "[    1.000000] WARNING: CPU: 0 PID: 1 at mm/x.c:1 f+0x1/0x2\n"
            "[    1.000001] Call Trace:\n"
            "[    1.000002] all is well\n"
        )
        shell()
    elif scenario == "systemd":
        systemd(json.loads(Path(sys.argv[2]).read_text()))
    else:
        raise SystemExit(f"no scenario {scenario}")


main(sys.argv[1])
