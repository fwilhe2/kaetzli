"""A list of in-guest checks typed as one line, whose answer names the first that failed.
For the boot tests that check many things once the guest has settled (test/gardener.py,
test/etc-upgrade.py): a failure then says which, not just that the line did not pass."""

from __future__ import annotations

from kaetzli.qemu import Guest

Checks = list[tuple[str, str]]  # (what a failure says, a bash test)


def line(items: Checks, mark: str, before: str = "") -> str:
    """`before`, then each test in turn: `<mark>-OK|` when all hold, `<mark>-FAIL: <what>|`
    at the first that does not. The `|` ends the answer, so a wait never takes half a
    message; each marker is split by quotes so the echo of the typed line cannot match.
    A `what` must not contain a quote or a `|`."""
    bad = [what for what, _ in items if "'" in what or "|" in what]
    if bad:
        raise ValueError(f"a check's message has a quote or a |: {bad}")
    return (
        before
        + " && ".join(
            f"{{ {test} || {{ echo \"{mark}-\"'FAIL: {what}|'; false; }}; }}"
            for what, test in items
        )
        + f" && echo {mark}'-OK|'"
    )


def verdict(guest: Guest, typed: str, mark: str, seconds: float, silence: str) -> None:
    """Type `typed` (a line()) and fail the test with the failed check's message, or with
    `silence` when no answer comes within `seconds`."""
    guest.send(typed)
    if not guest.wait(rf"{mark}-(OK|FAIL: [^|\r\n]*)\|", seconds):
        guest.fail(silence)
    if guest.group() != "OK":
        guest.fail(guest.group().removeprefix("FAIL: "))
