"""The launcher (go/cmd/kaetzli) boots the machine the harness boots.

Its qemu command line is a third copy of the pc profile's, after test/qemu-lib.sh and
kaetzli.qemu (docs/proposals/launcher.md). `kaetzli print-argv` prints what `start` would
run, and this holds it to kaetzli.qemu.Machine's for the same machine as tools/vm.sh sets
it up: a /var disk, extensions, the ssh forward and key, the hostname, reboot in place.
What is the launcher's own — its console and QMP on localhost TCP rather than vm.sh's
Unix sockets — is given to the Machine as its display arguments and as the tail.
"""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from kaetzli import repo
from kaetzli.qemu import Machine

needs_go = pytest.mark.skipif(not shutil.which("go"), reason="needs go")


@pytest.fixture(scope="module")
def launcher(tmp_path_factory) -> Path:
    if not shutil.which("go"):
        pytest.skip("needs go")
    out = tmp_path_factory.mktemp("bin") / "kaetzli"
    subprocess.run(
        ["go", "build", "-o", str(out), "./cmd/kaetzli"], cwd=repo.ROOT / "go", check=True
    )
    return out


@pytest.fixture
def image(tmp_path) -> Path:
    d = tmp_path / "image"
    files = {
        "rootfs.erofs": "",
        "bzImage": "",
        "rootfs.cmdline": "root=/dev/dm-0 ro rootfstype=erofs dm-mod.waitfor=/dev/vda "
        'dm-mod.create="root,,,ro,0 8 verity 1 /dev/vda /dev/vda 4096 4096 1 1 sha256 ab cd"\n',
        "extensions/podman.raw": "",
        "extensions/podman.dm-verity": "0 8 verity 1 @DEV@ @DEV@ 4096 4096 1 1 sha256 ef 01\n",
        "extensions/kubernetes.raw": "",
        "extensions/kubernetes.dm-verity": "0 9 verity 1 @DEV@ @DEV@ 4096 4096 1 1 sha256 23 45\n",
        "confexts/kubernetes.raw": "",
        "confexts/kubernetes.dm-verity": "0 1 verity 1 @DEV@ @DEV@ 4096 4096 1 1 sha256 67 89\n",
    }
    for name, content in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return d


def _both(launcher, image, tmp_path, *, arch, kvm, exts):
    var = tmp_path / "vm" / "var.img"
    var.parent.mkdir()
    var.write_bytes(b"")
    Path(f"{var}.machine-id").write_text("0123456789abcdef0123456789abcdef")
    pub = tmp_path / "vm" / "id_ed25519.pub"
    pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample kaetzli-dev@host\n")
    log = tmp_path / "vm" / "console.log"
    argv = ["print-argv", "--arch", arch, "--kvm", "1" if kvm else "0",
            "--mem", "3072", "--cpus", "4", "--image", str(image), "--var", str(var),
            "--ssh-port", "2201", "--pubkey", str(pub), "--hostname", "dev",
            "--console-port", "40001", "--qmp-port", "40002", "--log", str(log)]  # fmt: skip
    for e in exts:
        argv += ["--ext", e]
    got = json.loads(subprocess.run([str(launcher), *argv], capture_output=True, text=True,
                                    check=True).stdout)  # fmt: skip

    # The same machine, as tools/vm.sh's prepare() and qemu_ssh_setup make it.
    display = [
        "-display", "none", "-monitor", "none",
        "-chardev", f"socket,id=con,host=127.0.0.1,port=40001,server=on,wait=off,logfile={log}",
        "-serial", "chardev:con",
    ]  # fmt: skip
    m = Machine(
        rootfs=str(image / "rootfs.erofs"), kernel=str(image / "bzImage"),
        init="/usr/lib/systemd/systemd", arch=arch, profile="pc", mem="3072", cpus="4",
        var_disk=str(var), sysexts=list(exts), extensions=str(image),
        display_args=display, reboot=True, kvm=kvm,
    )  # fmt: skip
    m.nic_extra = ",hostfwd=tcp:127.0.0.1:2201-:22"
    m.append_extra = (
        " systemd.set_credential_binary=ssh.authorized_keys.root:"
        + base64.b64encode(pub.read_bytes()).decode()
        + " systemd.hostname=dev"
    )
    want = [m.qemu, *m.argv(), "-qmp", "tcp:127.0.0.1:40002,server=on,wait=off"]
    return got, want


@needs_go
@pytest.mark.parametrize("arch", ["amd64", "arm64"])
@pytest.mark.parametrize("kvm", [True, False])
@pytest.mark.parametrize("exts", [(), ("podman",), ("podman", "kubernetes")])
def test_the_launchers_command_line_is_the_harness(launcher, image, tmp_path, arch, kvm, exts):
    got, want = _both(launcher, image, tmp_path, arch=arch, kvm=kvm, exts=exts)
    assert got == want
