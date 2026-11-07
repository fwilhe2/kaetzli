"""test/secureboot.py: prove that Secure Boot seals the root: the signed UKI boots, its
command line is the one it carries whatever the VMM passes, and an unsigned one does not
boot at all.

    ./test/secureboot.py [rootfs.erofs]

Needs tools/sign-uki.sh's directory (SECUREBOOT, default output/secureboot) and the
unsigned output/kaetzli.efi beside the image. Overrides: TIMEOUT, MEM/CPUS, LOG.

Two boots, each with the same firmware and variables (our certificate enrolled as PK, KEK
and db, Secure Boot on):

  signed    The harness still passes its usual -append, with init=/bin/bash in it. Were
            that honoured, PID 1 would be a shell and no getty would ever ask for a login;
            the stub ignores it, so systemd starts, and the login goes through on the
            credential fw_cfg carried. Then: the kernel said "Secure boot enabled",
            /proc/cmdline is the UKI's (the root hash from rootfs.cmdline, no init=), and
            the ssh key credential arrived.
  unsigned  output/kaetzli.efi, the same UKI before signing: the firmware has to refuse
            it, so Linux never starts.

test/verity.py is the other half: that a root whose bytes differ from the hash does not
mount. This one shows nobody can hand the kernel a different hash.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from kaetzli.boot import boot, run
from kaetzli.qemu import Guest

MARKER = "SECUREBOOT-OK"
BEGIN = "SECUREBOOT-BEGIN"  # both split by quotes when typed, so only output matches
# OVMF's own chatter is not the kernel's; nothing of it is a kernel warning.
EXEMPT = "BdsDxe|SecureBoot"


def main(argv: list[str]) -> int:
    b = boot(
        [argv[0], "unused"] if argv else [], init="/bin/bash", log="output/secureboot-test.log"
    )
    m = b.machine
    m.init = "/bin/bash"  # the tamper: on the command line the stub must ignore
    m.secureboot = m.secureboot or "output/secureboot"

    def test() -> None:
        m.setup()
        m.preflight()
        m.ssh_setup("output/ssh-secureboot", port=None)
        root_cmdline = Path(m.root_cmdline_file()).read_text().strip()
        root_hash = root_cmdline.split(" sha256 ", 1)[1].split()[0]

        print(">> signed UKI, with init=/bin/bash passed from outside", flush=True)
        with b.guest("secureboot", exempt=EXEMPT) as g:
            if not g.wait("Secure boot enabled", g.deadline - g.clock()):
                g.fail("the kernel did not report Secure Boot on")
            g.login("root")  # a getty asked: systemd is PID 1, the init= was ignored
            g.mark()
            g.send(
                f"echo {BEGIN[:6]}'{BEGIN[6:]}'; cat /proc/cmdline; "
                f"ls /run/credentials/@system/; echo {MARKER[:6]}'{MARKER[6:]}'"
            )
            if not g.wait(MARKER, 60):
                g.fail("no answer from the guest")
            seen = g.log.read_bytes().decode(errors="replace")
            tail = seen[seen.rfind(BEGIN) : seen.rfind(MARKER)]
            if "init=/bin/bash" in tail:
                g.fail("the kernel took the command line passed from outside")
            if root_hash not in tail:
                g.fail(f"/proc/cmdline does not carry the image's root hash {root_hash}")
            if "ssh.authorized_keys.root" not in tail:
                g.fail("the ssh key credential did not arrive by fw_cfg")
            g.stop()
        print(
            ">> signed: Secure Boot on, the UKI's command line, credentials by fw_cfg", flush=True
        )

        print(">> unsigned UKI, the same firmware", flush=True)
        signed = os.path.join(m.secureboot, "kaetzli.efi")
        unsigned = os.path.join(os.path.dirname(m.rootfs), "kaetzli.efi")
        argv_ = m.argv()
        argv_[argv_.index(signed)] = unsigned
        b2 = b.log.replace(".log", "-unsigned.log")
        with Guest(m.qemu, argv_, b2, 90.0, name="secureboot-unsigned", exempt=EXEMPT) as g:
            status = g.watch("Linux version", 60)
            g.stop()
            if status == "match":
                g.fail("the firmware booted an unsigned kernel")
        print(">> unsigned: refused by the firmware, Linux never started", flush=True)

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
