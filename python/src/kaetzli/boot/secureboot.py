"""test/secureboot.py: prove that Secure Boot seals the root: the signed UKI boots, its
command line is the one it carries whatever the VMM passes, and an unsigned one does not
boot at all.

    ./test/secureboot.py [rootfs.erofs]

Needs tools/sign-uki.sh's directory (SECUREBOOT, default output/secureboot) and the
unsigned output/kaetzli.efi beside the image. Overrides: TIMEOUT, MEM/CPUS, LOG.

Three boots, the first two with our certificate enrolled as PK, KEK and db and Secure
Boot on:

  signed    The harness still passes its usual -append, with init=/bin/bash in it. Were
            that honoured, PID 1 would be a shell and no getty would ever ask for a login;
            the stub ignores it, so systemd starts, and the login goes through on the
            credential fw_cfg carried. Then: the kernel said "Secure boot enabled",
            /proc/cmdline is the UKI's (the root hash from rootfs.cmdline, no init=), the
            ssh key credential arrived, /var is the disk the credentials named
            (fstab.extra), with the machine ID kept beside it (system.machine_id), fetch
            says "Secure Boot: enabled",
            /var/lib/extensions, /var/lib/confexts and /var/lib/extensions.mutable are
            covered by an empty read-only tmpfs, so nothing on that unverified disk
            merges and /etc stays read-only, and — under a vTPM (swtpm)
            — PCR 11 is the value image/pcr.py predicts for that UKI, and, when it was
            sealed without extensions, the kaetzli.pcr.json published beside the image.
  unsigned  output/kaetzli.efi, the same UKI before signing: the firmware has to refuse
            it, so Linux never starts.
  control   The signed UKI again, with the firmware's default variables (Secure Boot
            off, ovmf-vars-off.fd). Now the stub takes the command line it is passed, so
            the kernel says Secure Boot is disabled, init=/bin/bash gives a shell, and fetch
            in it says so too. Were the injected argument ignored here too, the first
            boot's result would not be Secure Boot's doing, and the test would prove
            nothing: so that fails too.

test/verity.py is the other half: that a root whose bytes differ from the hash does not
mount. This one shows nobody can hand the kernel a different hash.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from kaetzli import repo
from kaetzli.boot import boot, run
from kaetzli.qemu import Guest, Refused

MARKER = "SECUREBOOT-OK"
PROMPT = "bash-[0-9]"
BEGIN = "SECUREBOOT-BEGIN"  # both split by quotes when typed, so only output matches
# OVMF's own chatter is not the kernel's; nothing of it is a kernel warning.
EXEMPT = "BdsDxe|SecureBoot"


def predicted_pcr11(uki: Path) -> str:
    """image/pcr.py's PCR 11 for this UKI (assembly-side code, loaded by path)."""
    spec = importlib.util.spec_from_file_location("pcr", repo.ROOT / "image" / "pcr.py")
    pcr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pcr)
    return pcr.pcr11(pcr.sections(uki.read_bytes()))


def published_pcr11(path: Path) -> str | None:
    """The image's kaetzli.pcr.json, as image/build-rootfs.sh wrote it, if it is there."""
    if not path.is_file():
        return None
    banks = json.loads(path.read_text())
    return next(e["hash"] for e in banks["sha256"] if e["pcr"] == 11)


@contextmanager
def swtpm() -> Iterator[str]:
    """A TPM 2.0 emulator for one boot: its control socket, removed after."""
    if not shutil.which("swtpm"):
        raise Refused("swtpm is not installed (Debian, Ubuntu, Fedora: swtpm)")
    with tempfile.TemporaryDirectory(prefix="swtpm.") as d:
        sock = os.path.join(d, "swtpm.sock")
        p = subprocess.Popen(
            ["swtpm", "socket", "--tpm2", "--tpmstate", f"dir={d}",
             "--ctrl", f"type=unixio,path={sock}", "--terminate"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        try:
            for _ in range(100):
                if os.path.exists(sock) or p.poll() is not None:
                    break
                time.sleep(0.05)
            if not os.path.exists(sock):
                raise Refused("swtpm did not start")
            yield sock
        finally:
            p.terminate()
            p.wait(timeout=10)


def signed_boot(b, m, root_hash: str, var_disk: str, expected: str) -> None:
    """The signed boot's checks; `expected` is PCR 11 as image/pcr.py predicts it."""
    with b.guest("secureboot", exempt=EXEMPT) as g:
        if not g.wait("Secure boot enabled", g.deadline - g.clock()):
            g.fail("the kernel did not report Secure Boot on")
        g.login("root")  # a getty asked: systemd is PID 1, the init= was ignored
        g.mark()
        g.send(
            f"echo {BEGIN[:6]}'{BEGIN[6:]}'; cat /proc/cmdline; "
            f"ls /run/credentials/@system/; findmnt -no SOURCE /var; cat /etc/machine-id; "
            "fetch --no-color | grep 'Secure Boot:'; "
            "echo SB-PCR11 $(cat /sys/class/tpm/tpm0/pcr-sha256/11); "
            "for d in extensions confexts extensions.mutable; do "
            'echo "SB-VARLIB $d $(findmnt -no FSTYPE,OPTIONS /var/lib/$d)"; done; '
            "touch /etc/sb-etc-test 2>/dev/null && echo SB-ETC-WRITABLE; "
            f"echo {MARKER[:6]}'{MARKER[6:]}'"
        )
        if not g.wait(MARKER, 60):
            g.fail("no answer from the guest")
        seen = g.log.read_bytes().decode(errors="replace")
        tail = seen[seen.rfind(BEGIN) : seen.rfind(MARKER)]
        if "init=/bin/bash" in tail:
            g.fail("the kernel took the command line passed from outside")
        if root_hash not in tail:
            g.fail(f"/proc/cmdline does not carry the image's root hash {root_hash}")
        if "Secure Boot: enabled" not in tail:
            g.fail("fetch does not say 'Secure Boot: enabled' (the SecureBoot variable)")
        if "ssh.authorized_keys.root" not in tail:
            g.fail("the ssh key credential did not arrive by fw_cfg")
        if "/dev/vdb" not in tail:
            g.fail("/var is not the disk the fstab.extra credential named")
        machine_id = Path(f"{var_disk}.machine-id").read_text().strip()
        if machine_id not in tail:
            g.fail(f"the machine ID is not the disk's {machine_id} (system.machine_id)")
        line = next((x for x in tail.splitlines() if x.startswith("SB-PCR11 ")), "")
        measured = line.removeprefix("SB-PCR11 ").strip().lower()
        if measured != expected:
            g.fail(
                f"PCR 11 is {measured or 'unreadable'} (/sys/class/tpm/tpm0/pcr-sha256/11), but "
                f"image/pcr.py predicts {expected}: the stub measured something else, or nothing"
            )
        if "SB-ETC-WRITABLE" in tail:
            g.fail(
                "/etc is writable under Secure Boot: its writes would land on the unverified "
                "/var disk (var-lib-extensions.mutable.mount, image/files/etc/systemd/confext.conf)"
            )
        for d in ("extensions", "confexts", "extensions.mutable"):
            line = next((x for x in tail.splitlines() if x.startswith(f"SB-VARLIB {d} ")), "")
            fstype, _, opts = line.removeprefix(f"SB-VARLIB {d} ").partition(" ")
            if fstype != "tmpfs" or "ro" not in opts.split(","):
                g.fail(
                    f"/var/lib/{d} is not covered by a read-only tmpfs under Secure Boot "
                    f"(findmnt: {line or 'nothing'}): an extension on /var would merge "
                    f"unverified (var-lib-{d}.mount)"
                )
        g.stop()


def main(argv: list[str]) -> int:
    b = boot(
        [argv[0], "unused"] if argv else [], init="/bin/bash", log="output/secureboot-test.log"
    )
    m = b.machine
    m.init = "/bin/bash"  # the tamper: on the command line the stub must ignore
    m.secureboot = m.secureboot or "output/secureboot"
    var_disk = m.var_disk or "output/secureboot-var.img"

    def test() -> None:
        # A fresh /var disk each run, unless VAR_DISK names one; only the signed boot has
        # it. Before setup(), which holds the boot to what the UKI was sealed for.
        if not m.var_disk:
            for f in (var_disk, f"{var_disk}.machine-id", f"{var_disk}.fstab"):
                Path(f).unlink(missing_ok=True)
        m.var_disk = var_disk
        m.setup()
        m.preflight()
        m.ssh_setup("output/ssh-secureboot", port=None)
        root_cmdline = Path(m.root_cmdline_file()).read_text().strip()
        root_hash = root_cmdline.split(" sha256 ", 1)[1].split()[0]

        uki = Path(m.secureboot) / "kaetzli.efi"
        expected = predicted_pcr11(uki)
        sealed = Path(m.secureboot) / "sealed"
        published = published_pcr11(Path(m.rootfs).with_name("kaetzli.pcr.json"))
        fields = dict(
            line.split("=", 1)
            for line in (sealed.read_text().splitlines() if sealed.is_file() else [])
            if "=" in line
        )
        # Signing adds a certificate, not a section: the signed UKI measures as the
        # unsigned one, whose value the image (and its release) publishes. One sealed with
        # extensions' tables has another command line, so another value.
        if published and not fields.get("extensions", "").split() and published != expected:
            raise Refused(
                f"kaetzli.pcr.json says PCR 11 is {published}, but image/pcr.py "
                f"predicts {expected} for {uki}: not the UKI that image was built with?"
            )

        print(">> signed UKI, with init=/bin/bash passed from outside, under a vTPM", flush=True)
        with swtpm() as sock:
            m.tpm_socket = sock
            signed_boot(b, m, root_hash, var_disk, expected)
            m.tpm_socket = ""
        print(
            ">> signed: Secure Boot on, the UKI's command line, credentials by fw_cfg, "
            f"/var on its disk, PCR 11 {expected[:16]}… as predicted"
            + (" and published" if published else ""),
            flush=True,
        )
        m.var_disk = ""

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

        print(">> control: the signed UKI with Secure Boot off", flush=True)
        m.secureboot_vars = "ovmf-vars-off.fd"
        if not os.path.isfile(os.path.join(m.secureboot, m.secureboot_vars)):
            raise Refused(
                f"missing {m.secureboot}/{m.secureboot_vars} (tools/sign-uki.sh makes it)"
            )
        log = b.log.replace(".log", "-control.log")
        with Guest(m.qemu, m.argv(), log, float(b.timeout), name="secureboot-control") as g:
            if not g.wait("Secure boot disabled", g.deadline - g.clock()):
                g.fail("with Secure Boot off in the variables, the kernel did not say so")
            status = g.watch(f"{PROMPT}|login:", g.deadline - g.clock())
            if status != "match" or "login" in g.group(0):
                g.fail(
                    "without Secure Boot the injected init=/bin/bash was not honoured either: "
                    "the signed boot's sealed command line is not Secure Boot's doing"
                )
            # bash is PID 1, so nothing mounted /sys or efivarfs: what systemd would. It
            # read /etc/bashrc (interactive), whose grep alias colours the match; `command`
            # skips it (an unalias on the same line would not: bash expands aliases as it
            # reads the line, before running any of it).
            g.mark()
            g.send(
                "mount -t sysfs sysfs /sys; mount -t efivarfs efivarfs /sys/firmware/efi/efivars; "
                f"fetch --no-color | command grep 'Secure Boot:'; echo {MARKER[:6]}'{MARKER[6:]}'"
            )
            if not g.wait(MARKER, 30):
                g.fail("no answer from the shell")
            if "Secure Boot: disabled" not in (g.before or ""):
                g.fail(f"with Secure Boot off, fetch does not say so: {g.before!r}")
            g.stop()
        print(">> control: Secure Boot off, the injected init= gave a shell", flush=True)

    return run(test)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
