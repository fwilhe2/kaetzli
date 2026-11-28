"""test/gardener-join.py: test/gardener.py's node, provisioned the same way, against a real
API server, past where that test stops: node-agent gets its client certificate, reads an
OperatingSystemConfig from a Secret, writes its files and units, and the kubelet it
starts registers the Node, which node-agent then marks as having applied that OSC.

    ./test/gardener-join.py [rootfs.erofs] [bzImage]

Overrides, from the environment: TIMEOUT, MEM/CPUS, LOG (default
output/gardener-join-test.log), and tools/k8s-demo.py's API_PORT, CP_MEM, ROUTER_MEM,
VAR_SIZE. Needs KVM (it skips without, as test/k8s.py does), the kubernetes extension, and
the network: node-agent from Gardener's registry, the control plane from registry.k8s.io.

The API server is a kubeadm control plane from tools/k8s-demo.py (a router and one node,
kaetzli too), standing in for a shoot's: Gardener's own node-agent and init.sh do not
know the difference. It is reached from the Gardener node through the host, the API
server forward k8s-demo already has (127.0.0.1:API_PORT), as API_IP:6443 inside the
node's own user network (a guestfwd relay beside the metadata service), an address added to
the API server's certificate. What Gardener's control plane would do for the node, the
test does by hand:

  - a kubeadm bootstrap token for the user data, so node-agent's and kubelet's
    certificate requests are allowed, and kubelet's approved by kubeadm's own rules
  - node-agent's request (signer kube-apiserver-client, from the bootstrap token)
    approved, as gardener-resource-manager would
  - cluster-admin for the group node-agent's certificate names
    (gardener.cloud:node-agents), where Gardener has its node-agent-authorizer webhook
  - the OSC Secret in kube-system: one file in /etc, kubelet's configuration, and a
    kubelet.service shaped like Gardener's own (its flags and paths, kubelet's bootstrap
    kubeconfig written by node-agent), running the extension's /usr/bin/kubelet rather
    than one copied from an image

What is checked: on the API server, a Node registered by the kubelet node-agent started,
with node-agent's checksum/cloud-config-data annotation equal to the Secret's
checksum/data-script; on the node, node-agent's kubeconfig, the OSC's file in /etc's upper
layer, its kubelet.service in /etc/systemd/system overriding the extension's and active,
and nothing that hit a read-only filesystem. The Node stays NotReady (no pod network):
that is not under test.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from kaetzli import k8s_demo
from kaetzli.boot import boot, gardener, run
from kaetzli.boot.checks import line, verdict
from kaetzli.boot.user_data import metadata_dir, nic
from kaetzli.qemu import Refused
from kaetzli.qemu.machine import kvm_usable

API_IP = "169.254.169.253"  # in the node's user network; not its gateway, not the metadata service
API_SERVER = f"https://{API_IP}:6443"
NODE = "kaetzli"  # the node's hostname, so kubelet's Node name
OSC_FILE = "/etc/kaetzli-osc-test"
OSC_FILE_DATA = "written by gardener-node-agent from the OSC\n"

KUBELET_CONFIG = """apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
cgroupDriver: systemd
authentication:
  anonymous: {enabled: false}
  webhook: {enabled: true}
  x509: {clientCAFile: /var/lib/gardener-node-agent/cluster-ca.crt}
authorization: {mode: Webhook}
clusterDNS: [10.96.0.10]
clusterDomain: cluster.local
rotateCertificates: true
"""

# Gardener's kubelet unit (components/kubelet: its paths and flags), with the extension's
# kubelet instead of /opt/bin's.
KUBELET_UNIT = """[Unit]
Description=kubelet daemon
Documentation=https://kubernetes.io/docs/admin/kubelet
After=containerd.service
[Install]
WantedBy=multi-user.target
[Service]
Restart=always
RestartSec=5
EnvironmentFile=/etc/environment
EnvironmentFile=-/var/lib/kubelet/extra_args
ExecStart=/usr/bin/kubelet \\
    --bootstrap-kubeconfig=/var/lib/kubelet/kubeconfig-bootstrap \\
    --config=/var/lib/kubelet/config/kubelet \\
    --kubeconfig=/var/lib/kubelet/kubeconfig-real \\
    --container-runtime-endpoint=unix:///run/containerd/containerd.sock \\
    --v=2 $KUBELET_EXTRA_ARGS
"""


def osc() -> dict:
    """The OperatingSystemConfig node-agent reads from the Secret: extensions.gardener.cloud
    v1alpha1, as the OS extension's actuator leaves it, with only what this needs."""

    def inline(data: str) -> dict:
        return {"inline": {"encoding": "b64", "data": base64.b64encode(data.encode()).decode()}}

    return {
        "apiVersion": "extensions.gardener.cloud/v1alpha1",
        "kind": "OperatingSystemConfig",
        "metadata": {"name": gardener.SECRET_NAME, "namespace": "kube-system"},
        "spec": {
            "type": "kaetzli",
            "purpose": "reconcile",
            "files": [
                {"path": OSC_FILE, "permissions": 0o644, "content": inline(OSC_FILE_DATA)},
                {
                    "path": "/var/lib/kubelet/config/kubelet",
                    "permissions": 0o600,
                    "content": inline(KUBELET_CONFIG),
                },
            ],
            "units": [
                {
                    "name": "kubelet.service",
                    "command": "start",
                    "enable": True,
                    "content": KUBELET_UNIT,
                },
            ],
        },
    }


def secret(osc_yaml: str) -> tuple[dict, str]:
    """The Secret node-agent watches, and the checksum it carries (and node-agent copies
    to the Node once it has applied it)."""
    checksum = hashlib.sha256(osc_yaml.encode()).hexdigest()
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": gardener.SECRET_NAME,
            "namespace": "kube-system",
            "annotations": {"checksum/data-script": checksum},
        },
        "data": {"osc.yaml": base64.b64encode(osc_yaml.encode()).decode()},
    }, checksum


NODE_AGENTS = {
    "apiVersion": "rbac.authorization.k8s.io/v1",
    "kind": "ClusterRoleBinding",
    "metadata": {"name": "kaetzli-test-node-agents"},
    "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole",
                "name": "cluster-admin"},
    "subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group",
                  "name": "gardener.cloud:node-agents"}],
}  # fmt: skip

CHECKS = [
    ("node-agent has no kubeconfig of its own (its certificate was not issued)",
     f"[ -s {gardener.BASE}/credentials/kubeconfig ]"),
    (f"the OSC file {OSC_FILE} is missing or wrong",
     f"[[ $(<{OSC_FILE}) == {OSC_FILE_DATA.strip()!r} ]]"),
    ("the OSC file is not in the upper layer of /etc",
     f"[ -f /var/lib/extensions.mutable{OSC_FILE} ]"),
    ("kubelet.service is not the OSC one in /etc/systemd/system",
     "[[ $(systemctl show -P FragmentPath kubelet.service) == "
     "/etc/systemd/system/kubelet.service ]]"),
    ("kubelet.service is not active", "[[ $(systemctl is-active kubelet.service) == active ]]"),
    ("kubelet has no client certificate from its bootstrap",
     "[ -s /var/lib/kubelet/kubeconfig-real ]"),
    ("gardener-node-agent.service is not active",
     "[[ $(systemctl is-active gardener-node-agent.service) == active ]]"),
    ("something hit a read-only filesystem",
     "! journalctl -b -q | grep -q 'Read-only file system'"),
]  # fmt: skip

DIAGNOSE = (
    "systemctl --no-pager status gardener-node-agent.service kubelet.service | head -40; "
    "journalctl --no-pager -b -u gardener-node-agent -n 40; "
    "journalctl --no-pager -b -u kubelet -n 20; "
    f"ls -la {gardener.BASE} {gardener.BASE}/credentials /var/lib/kubelet"
)


def approve_node_agent(k, stop: threading.Event, token_id: str, every: float = 3) -> None:
    """What gardener-resource-manager does: approve node-agent's certificate request,
    which only its bootstrap token can have made. kubelet's are kubeadm's to approve."""
    who = f"system:bootstrap:{token_id}"
    while not stop.wait(every):
        listed = k("get", "csr", "-o", "json", capture=True)
        if listed.returncode:
            continue
        for item in json.loads(listed.stdout or b"{}").get("items", []):
            spec, status = item.get("spec", {}), item.get("status", {})
            if (
                spec.get("username") == who
                and spec.get("signerName") == "kubernetes.io/kube-apiserver-client"
                and not status.get("conditions")
            ):
                name = item["metadata"]["name"]
                k("certificate", "approve", name, capture=True)
                print(f">> approved node-agent's certificate request {name}", flush=True)


def main(argv: list[str]) -> int:
    b = boot(argv, init="/usr/lib/systemd/systemd", log="output/gardener-join-test.log")
    if not kvm_usable():
        print("note: no writable /dev/kvm on this host, skipping the Gardener join check")
        return 0
    root = Path(tempfile.mkdtemp())
    disk = Path(os.path.dirname(b.log) or ".") / "gardener-join-test.img"
    env = {**os.environ, "ROOTFS": b.machine.rootfs, "KERNEL": b.machine.kernel}
    stop = threading.Event()

    def kubectl(*args: str, input: bytes | None = None, capture: bool = False):
        return k8s_demo.k(*args, input=input, capture=capture)

    def must(p: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
        if p.returncode:
            raise Refused(f"kubectl {p.args[-1] if p.args else ''} failed on the control plane")
        return p

    def apply(obj: dict) -> None:
        must(kubectl("apply", "-f", "-", input=json.dumps(obj).encode(), capture=True))

    def control_plane() -> tuple[str, str, str]:
        """A kubeadm control plane (k8s-demo's, one node); returns the CA, a bootstrap
        token and the Secret's checksum."""
        cluster = k8s_demo.Cluster.from_env(k8s_demo.base_machine(env), env)
        cluster.nodes = 1
        cluster.cert_sans = (API_IP,)
        k8s_demo.say("Control plane: kubeadm on k8s-1, behind k8s-demo's router")
        cluster.boot()
        cluster.init_control_plane()
        ca = k8s_demo.out(1, "cat", "/etc/kubernetes/pki/ca.crt") + "\n"
        token = k8s_demo.out(1, "kubeadm", "token", "create", "--ttl", "2h").strip()
        apply(NODE_AGENTS)
        osc_yaml = json.dumps(osc(), indent=2)  # JSON is YAML
        obj, checksum = secret(osc_yaml)
        apply(obj)
        return ca, token, checksum

    def test() -> None:
        m = b.machine
        m.sysexts = ["kubernetes"]
        m.var_disk = str(disk)
        if m.profile != "pc":
            raise Refused(
                f"test/gardener-join.py boots the pc profile, not QEMU_PROFILE={m.profile}"
            )
        disk.unlink(missing_ok=True)
        Path(f"{disk}.machine-id").unlink(missing_ok=True)
        ca, token, checksum = control_plane()

        approver = threading.Thread(
            target=approve_node_agent, args=(kubectl, stop, token.split(".")[0]), daemon=True
        )
        approver.start()

        metadata_dir(root)
        (root / "openstack" / "latest" / "user_data").write_text(
            gardener.user_data(cluster_ca=ca, bootstrap_token=token, api_server=API_SERVER)
        )
        api_port = env.get("API_PORT") or "6443"
        m.nic_args = nic(root, ((API_IP, 6443, int(api_port)),))
        m.append_extra += " kaetzli.user-data=openstack"
        m.setup()
        m.preflight()
        k8s_demo.say("The Gardener node: user data, node init, node-agent against that API server")
        with b.guest("gardener-join", diagnose=DIAGNOSE) as guest:
            guest.login()
            until = time.monotonic() + 600
            annotation = ""
            while time.monotonic() < until:
                got = kubectl(
                    "get", "node", NODE, "-o",
                    "jsonpath={.metadata.annotations.checksum/cloud-config-data}", capture=True,
                )  # fmt: skip
                annotation = got.stdout.decode().strip() if got.returncode == 0 else ""
                if annotation:
                    break
                guest.drain(5)
            if not annotation:
                registered = kubectl("get", "node", NODE, capture=True).returncode == 0
                guest.fail(
                    f"node-agent never marked Node {NODE} as having applied the OSC"
                    if registered
                    else f"no Node {NODE}: kubelet from the OSC never registered"
                )
            if annotation != checksum:
                guest.fail(
                    f"Node {NODE} carries OSC checksum {annotation}, not the Secret's {checksum}"
                )
            print(f">> Node {NODE} registered and marked with the OSC's checksum", flush=True)
            verdict(guest, line(CHECKS, "JOIN"), "JOIN", 120, "no answer from the node's checks")
            kubectl("get", "nodes", "-o", "wide")
            print(
                f">> Gardener {gardener.VERSION} join OK: node-agent got its certificate, "
                "applied the OSC (a file in /etc, kubelet.service in /etc/systemd/system "
                "over the extension's), and its kubelet registered the Node",
                flush=True,
            )

    try:
        return run(test)
    finally:
        stop.set()
        for log in sorted(k8s_demo.STATE.glob("*.console.log")):
            shutil.copyfile(
                log, f"output/gardener-join-{log.name.removesuffix('.console.log')}.log"
            )
        k8s_demo.down()
        shutil.rmtree(root, ignore_errors=True)
        disk.unlink(missing_ok=True)
        Path(f"{disk}.machine-id").unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
