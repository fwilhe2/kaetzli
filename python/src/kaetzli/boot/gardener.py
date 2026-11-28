"""test/gardener.py: provision a kaetzli VM the way Gardener provisions a worker, up to the
point where it needs a shoot's API server. Real Gardener code from the release pinned
below, not a stand-in: its gardener-node-init.service and init.sh, then the node-agent
binary from Gardener's registry, whose `bootstrap` writes, enables and starts
gardener-node-agent.service and disables gardener-node-init.

    ./test/gardener.py [rootfs.erofs] [bzImage]

Overrides, from the environment: TIMEOUT (each boot), MEM/CPUS, LOG (default
output/gardener-test.log, written as gardener-test-first.log and -second.log), as in
test/podman.py. Needs the kubernetes extension
(containerd and ctr) and the network: the image comes from europe-docker.pkg.dev.

The user data is what an OS extension makes of the OperatingSystemConfig, in the shape
of Gardener's own (gardener-extension-os-kaetzli does not exist yet; this is
provider-local's actuator, extensions/pkg/controller/operatingsystemconfig/bash.go):
every OSC file written with `cat << EOF`, base64 unless transmitted unencoded, and
chmodded; every unit into /etc/systemd/system; `systemctl daemon-reload`, then each unit
enabled and restarted --no-block; the whole wrapped to run once
(/var/lib/osc/provision-osc-applied). The OSC is nodeinit.Config's: the cluster CA, the
bootstrap token and machine name machine-controller-manager would substitute, init.sh,
and node-agent's configuration file. It is served by test/user-data.py's stand-in
metadata service and run by kaetzli-user-data.service.

What is checked once bootstrap says it finished:

  - gardener-node-init ran and is disabled; gardener-node-agent.service is in
    /etc/systemd/system (the writable /etc), enabled and was started
  - the binary is in /opt/bin, which is /var/opt/bin, and runs on this image
  - nothing in the journal hit a read-only filesystem

node-agent then fails to reach the API server (api.kaetzli-test.invalid never resolves)
and restarts every 5s; that is expected here, so the test stops it before reporting. What
it would do next, against a real control plane, is the proposal's open question
(docs/proposals/gardener-node.md).

/var is a disk (output/gardener-test.img), and the machine is powered off and booted a
second time from it, as a cloud reboots a node. What is checked then:

  - the user data did not run again: kaetzli-user-data.service found this instance
    recorded and never asked for user_data, and gardener-node-init did not start
  - gardener-node-agent.service started from what the first boot left: its unit in /etc's
    upper layer (/var/lib/extensions.mutable/etc) and its binary in /var/opt/bin. It did
    not, before systemd-confext's drop-in (50-mutable-etc.conf) re-queued the boot's
    targets: the boot's transaction is made before the merge
  - again, nothing hit a read-only filesystem

INIT_SH is Gardener's nodeinit/templates/scripts/init.tpl.sh at VERSION, rendered with
Go's text/template exactly as nodeinit.generateInitScript does without a registry CA (the
image, binaryName gardener-node-agent, binaryDirectory /opt/bin, configDir
/var/lib/gardener-node-agent). A Gardener bump re-renders it and moves IMAGE to the new
tag's index digest (`curl -sI` the manifest with an image-index Accept header).
"""

# INIT_SH is Gardener's script verbatim, two of its lines longer than ours may be.
# ruff: noqa: E501

from __future__ import annotations

import base64
import os
import shutil
import sys
import tempfile
from pathlib import Path

from kaetzli import repo
from kaetzli.boot import boot, run
from kaetzli.boot.checks import Checks, verdict
from kaetzli.boot.checks import line as checks_line
from kaetzli.boot.persistent_var import shutdown
from kaetzli.boot.user_data import metadata_dir, nic
from kaetzli.qemu import Refused

VERSION = "v1.154.6"
IMAGE = (
    f"europe-docker.pkg.dev/gardener-project/releases/gardener/node-agent:{VERSION}"
    "@sha256:4706369040b1fa551549e05d3f85c2c651ed3f486ea079f80c7e064aceb425e6"
)

BASE = "/var/lib/gardener-node-agent"  # nodeagentconfigv1alpha1.BaseDir
UNIT = "gardener-node-init.service"  # InitUnitName

INIT_SH = f"""#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

image="{IMAGE}"

echo "> Prepare temporary directory for image pull and mount"
tmp_dir="$(mktemp -d)"
unmount() {{
  ctr images unmount "$tmp_dir" && rm -rf "$tmp_dir"
}}
trap unmount EXIT

echo "> Pull gardener-node-agent image and mount it to the temporary directory"
CTR_MAJOR=$(ctr version | grep Version | tail -n1 | awk '{{print $2}}' | cut -d '.' -f 1 | sed 's/[a-zA-Z]//g')
CTR_EXTRA_ARGS=""
if [ "$CTR_MAJOR" -gt 1 ]; then
    CTR_EXTRA_ARGS="--skip-metadata"
fi
ctr images pull $CTR_EXTRA_ARGS --hosts-dir "/etc/containerd/certs.d" "$image"
ctr images mount "$image" "$tmp_dir"

echo "> Copy gardener-node-agent binary to host (/opt/bin) and make it executable"
mkdir -p "/opt/bin"
cp -f "$tmp_dir/gardener-node-agent" "/opt/bin" || cp -f "$tmp_dir/ko-app/gardener-node-agent" "/opt/bin"
chmod +x "/opt/bin/gardener-node-agent"

echo "> Bootstrap gardener-node-agent"
exec "/opt/bin/gardener-node-agent" bootstrap --config-dir="{BASE}"
"""

# nodeinit.generateInitScriptUnit, without a registry CA.
INIT_UNIT = f"""[Unit]
Description=Downloads the gardener-node-agent binary from the container registry and bootstraps it.
Requires=containerd.service
After=containerd.service
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
Restart=on-failure
RestartSec=5
StartLimitBurst=0
EnvironmentFile=/etc/environment
ExecStart={BASE}/init.sh
StandardOutput=journal+console
StandardError=journal+console
[Install]
WantedBy=multi-user.target"""

# A self-signed CA whose key was thrown away when it was made (openssl req -x509 -keyout
# /dev/null): a certificate node-agent can parse, for a server that does not exist.
CLUSTER_CA = """-----BEGIN CERTIFICATE-----
MIIBezCCASCgAwIBAgIUJALPVE35+Los2QiSuxyX7xoPHmgwCgYIKoZIzj0EAwIw
IjEgMB4GA1UEAwwXa2FldHpsaS10ZXN0LW5vLWNsdXN0ZXIwIBcNMjYxMTI4MDgy
MDA0WhgPMjEyNjExMDQwODIwMDRaMCIxIDAeBgNVBAMMF2thZXR6bGktdGVzdC1u
by1jbHVzdGVyMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEScJMeaYKvN9BJKK/
9sN5b38zIZshsaTxeqOt3AodWl9o3Hzfhz9XWFqmcfuXCagxGQmUUMj0GYMHYxc9
fH4ry6MyMDAwHQYDVR0OBBYEFLCifKqBu5isUXvMINhN8yOG1cdSMA8GA1UdEwEB
/wQFMAMBAf8wCgYIKoZIzj0EAwIDSQAwRgIhAKFdG+UL2RrjGDhiLRavq4xTdHEQ
jLtFQ/j31mqql9PZAiEA/rbYYr6aDTP+2dfONuPsMaU9K23jlIHAwZMkAhG+PRM=
-----END CERTIFICATE-----
"""


def kubelet_version() -> str:
    """The kubernetes extension's kubelet, which node-agent's configuration names."""
    (version,) = repo.env_get("kubernetes", "VERSION")
    return version


def config() -> str:
    """node-agent's configuration, as nodeagent.ComponentConfig makes it for a worker
    pool, with every sync period set rather than left to defaulting."""
    return f"""apiVersion: nodeagent.config.gardener.cloud/v1alpha1
kind: NodeAgentConfiguration
clientConnection:
  qps: 100
  burst: 130
apiServer:
  server: https://api.kaetzli-test.invalid
  caFile: {BASE}/cluster-ca.crt
controllers:
  operatingSystemConfig:
    secretName: gardener-node-agent-kaetzli-test
    kubernetesVersion: {kubelet_version()}
    syncPeriod: 1m
    preferIPv6: false
  token:
    syncPeriod: 12h
  systemdUnitCheck:
    syncPeriod: 15s
"""


def _cat(path: str, data: str, unencoded: bool = False) -> str:
    """bash.go's catDataIntoFile."""
    if unencoded:
        return f'\ncat << EOF > "{path}"\n{data}\nEOF'
    return f'\ncat << EOF | base64 -d > "{path}"\n{base64.b64encode(data.encode()).decode()}\nEOF'


def user_data() -> str:
    files = [  # (path, mode, data, transmitted unencoded)
        (f"{BASE}/cluster-ca.crt", 0o640, CLUSTER_CA, False),
        (f"{BASE}/credentials/bootstrap-token", 0o640, "abcdef.0123456789abcdef", True),
        (f"{BASE}/init.sh", 0o755, INIT_SH, False),
        (f"{BASE}/machine-name", 0o640, "kaetzli-gardener-test", True),
        (f"{BASE}/config-{VERSION}.yaml", 0o600, config(), False),
    ]
    out = ""
    for path, mode, data, unencoded in files:  # bash.go's FilesToDiskScript
        out += f'\nmkdir -p "{os.path.dirname(path)}"\n' + _cat(path, data, unencoded)
        out += f'\nchmod "{mode:04o}" "{path}"'
    units = _cat(f"/etc/systemd/system/{UNIT}", INIT_UNIT)  # UnitsToDiskScript
    script = (
        f"#!/bin/bash\n{out}\n{units}\nsystemctl daemon-reload\n"
        f"systemctl enable '{UNIT}' && systemctl restart --no-block '{UNIT}'\n"
    )
    # WrapProvisionOSCIntoOneshotScript: after the leading comment lines (the #! here).
    first, rest = script.split("\n", 1)
    return "\n".join(
        [
            first,
            'if [ -f "/var/lib/osc/provision-osc-applied" ]; then',
            '  echo "Provision OSC already applied, exiting..."',
            "  exit 0",
            "fi",
            "",
            rest,
            "",
            "mkdir -p /var/lib/osc",
            "touch /var/lib/osc/provision-osc-applied",
            "",
        ]
    )


# What must hold once bootstrap has finished, each with what the failure says.
CHECKS = [
    ("kaetzli-user-data.service is not active",
     "[[ $(systemctl is-active kaetzli-user-data.service) == active ]]"),
    ("the user data did not finish (no /var/lib/osc/provision-osc-applied)",
     "[ -f /var/lib/osc/provision-osc-applied ]"),
    ("the node-agent bootstrap never finished",
     "journalctl -b -q -u gardener-node-init | grep -q 'Bootstrap procedure finished'"),
    (f"{UNIT} is still enabled", f"[[ $(systemctl is-enabled {UNIT}) == disabled ]]"),
    ("bootstrap wrote no /etc/systemd/system/gardener-node-agent.service",
     "[ -f /etc/systemd/system/gardener-node-agent.service ]"),
    ("gardener-node-agent.service is not enabled",
     "[[ $(systemctl is-enabled gardener-node-agent.service) == enabled ]]"),
    ("gardener-node-agent.service was never started",
     "journalctl -b -q -u gardener-node-agent | grep -q 'Started.*Gardener Node Agent'"),
    ("/opt/bin is not /var/opt/bin", "[[ $(readlink -f /opt/bin) == /var/opt/bin ]]"),
    ("/opt/bin/gardener-node-agent does not run", "/opt/bin/gardener-node-agent --help >/dev/null"),
    ("something hit a read-only filesystem",
     "! journalctl -b -q | grep -q 'Read-only file system'"),
]  # fmt: skip

# The second boot, from the first one's /var: nothing provisioned again, node-agent back.
SECOND_CHECKS = [
    ("kaetzli-user-data.service is not active",
     "[[ $(systemctl is-active kaetzli-user-data.service) == active ]]"),
    ("the user data ran again (kaetzli-user-data did not find this instance recorded)",
     "journalctl -b -q -u kaetzli-user-data | grep -q 'user data already ran for this instance'"),
    (f"{UNIT} started again", f"[[ -z $(journalctl -b -q -u {UNIT}) ]]"),
    (f"{UNIT} is enabled again", f"[[ $(systemctl is-enabled {UNIT}) == disabled ]]"),
    ("gardener-node-agent.service is not in the upper layer of /etc",
     "[ -f /var/lib/extensions.mutable/etc/systemd/system/gardener-node-agent.service ]"),
    ("gardener-node-agent.service did not start on the second boot",
     "journalctl -b -q -u gardener-node-agent | grep -q 'Started.*Gardener Node Agent'"),
    ("/opt/bin/gardener-node-agent is gone or does not run",
     "/opt/bin/gardener-node-agent --help >/dev/null"),
    ("something hit a read-only filesystem",
     "! journalctl -b -q | grep -q 'Read-only file system'"),
]  # fmt: skip


def checks(items: Checks, wait_for: str, mark: str, tries: int) -> str:
    """Wait until `wait_for` (a bash test) holds, `tries` times 2s at most, stop the node
    agent (it cannot reach an API server and would keep writing to the console), then run
    each check, naming the first that fails (kaetzli.boot.checks)."""
    return checks_line(
        items,
        mark,
        before=(
            f"for i in $(seq {tries}); do {{ {wait_for}; }} && break; sleep 2; done; "
            "systemctl stop gardener-node-agent.service; "
        ),
    )


CHECK = checks(
    CHECKS,
    "journalctl -b -q -u gardener-node-init | grep -q 'Bootstrap procedure finished'",
    "GARDENER",
    150,
)
SECOND_CHECK = checks(
    SECOND_CHECKS,
    "journalctl -b -q -u gardener-node-agent | grep -q 'Started.*Gardener Node Agent'",
    "REBOOT",
    60,  # it starts with the boot or not at all
)

DIAGNOSE = (
    "systemctl --no-pager status kaetzli-user-data.service containerd.service "
    f"{UNIT} gardener-node-agent.service; ls -la /opt/bin/ {BASE}; "
    "journalctl --no-pager -b -u kaetzli-user-data -u gardener-node-init -n 60; "
    "journalctl --no-pager -b -q | grep 'Read-only file system' | head"
)


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="")
    log_base = os.environ.get("LOG", "output/gardener-test.log").removesuffix(".log")
    disk = Path(os.path.dirname(log_base) or ".") / "gardener-test.img"
    root = Path(tempfile.mkdtemp())

    def remove_disk() -> None:
        disk.unlink(missing_ok=True)
        Path(f"{disk}.machine-id").unlink(missing_ok=True)

    def start(name: str):
        m = b.machine
        m.setup()
        m.preflight()
        b.log = f"{log_base}-{name}.log"
        return b.guest("gardener", diagnose=DIAGNOSE)

    def test() -> None:
        m = b.machine
        m.sysexts = ["kubernetes"]
        m.var_disk = str(disk)
        if m.profile != "pc":
            raise Refused(f"test/gardener.py boots the pc profile, not QEMU_PROFILE={m.profile}")
        remove_disk()
        metadata_dir(root)
        (root / "openstack" / "latest" / "user_data").write_text(user_data())
        requests = root / "requests.log"
        m.nic_args = nic(root)
        m.append_extra += " kaetzli.user-data=openstack"

        with start("first") as guest:
            guest.login()
            verdict(guest, CHECK, "GARDENER", 420, "Gardener's node init did not finish")
            print(
                f">> Gardener {VERSION} node init OK: init.sh pulled node-agent with ctr, "
                "installed it into /opt/bin, and its bootstrap wrote, enabled and started "
                "gardener-node-agent.service in /etc and disabled gardener-node-init",
                flush=True,
            )
            shutdown(guest)

        requests.unlink(missing_ok=True)
        with start("second") as guest:
            guest.login()
            verdict(guest, SECOND_CHECK, "REBOOT", 360, "node-agent did not come back")
            asked = requests.read_text().split() if requests.exists() else []
            if "/openstack/latest/user_data" in asked:
                guest.fail(f"the second boot asked for the user data again (asked: {asked})")
            print(
                ">> reboot OK: the same /var, user data not fetched or run again, "
                "gardener-node-init not started, gardener-node-agent.service back from "
                "/etc's upper layer and /var/opt/bin",
                flush=True,
            )

    try:
        return run(test)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        remove_disk()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
