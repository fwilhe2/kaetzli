"""The qemu harness: the machine a boot test starts (machine) and the guest it then
drives over the console (console). The Python side of test/qemu-lib.sh, beside it until
every boot test has moved (docs/proposals/host-scripting.md, item 7)."""

from kaetzli.qemu.console import Failed, Guest
from kaetzli.qemu.machine import Machine, Refused, refuse

__all__ = ["Failed", "Guest", "Machine", "Refused", "refuse"]
