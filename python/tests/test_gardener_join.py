"""test/gardener-join.py's pieces that need no cluster: the OSC and its Secret, the line
typed at the node, and which certificate requests it approves."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import threading

from kaetzli.boot import gardener, gardener_join
from kaetzli.boot.checks import line


def test_the_secret_carries_the_osc_and_its_checksum():
    osc_yaml = json.dumps(gardener_join.osc())
    obj, checksum = gardener_join.secret(osc_yaml)
    assert obj["metadata"]["name"] == gardener.SECRET_NAME
    assert obj["metadata"]["namespace"] == "kube-system"
    assert obj["metadata"]["annotations"] == {"checksum/data-script": checksum}
    assert base64.b64decode(obj["data"]["osc.yaml"]).decode() == osc_yaml
    assert checksum == hashlib.sha256(osc_yaml.encode()).hexdigest()


def test_the_osc_writes_a_file_in_etc_and_starts_gardeners_shape_of_kubelet():
    spec = gardener_join.osc()["spec"]
    files = {f["path"]: f for f in spec["files"]}
    data = base64.b64decode(files[gardener_join.OSC_FILE]["content"]["inline"]["data"]).decode()
    assert data == gardener_join.OSC_FILE_DATA
    assert files[gardener_join.OSC_FILE]["permissions"] == 0o644
    assert "/var/lib/kubelet/config/kubelet" in files
    (unit,) = spec["units"]
    assert unit["name"] == "kubelet.service" and unit["enable"] and unit["command"] == "start"
    for flag in (
        "--bootstrap-kubeconfig=/var/lib/kubelet/kubeconfig-bootstrap",
        "--kubeconfig=/var/lib/kubelet/kubeconfig-real",
        "--config=/var/lib/kubelet/config/kubelet",
    ):
        assert flag in unit["content"]


def test_the_user_data_points_node_agent_at_the_given_api_server():
    script = gardener.user_data(
        cluster_ca="CA\n", bootstrap_token="tok123.0123456789abcdef", api_server="https://x:6443"
    )
    assert "tok123.0123456789abcdef" in script
    assert base64.b64encode(gardener.config("https://x:6443").encode()).decode() in script


def test_the_node_checks_are_one_bash_command():
    typed = line(gardener_join.CHECKS, "JOIN")
    assert subprocess.run(["bash", "-n", "-c", typed]).returncode == 0


def test_only_node_agents_pending_request_is_approved():
    def csr(name, user, signer, conditions=None):
        return {
            "metadata": {"name": name},
            "spec": {"username": user, "signerName": signer},
            "status": {"conditions": conditions} if conditions else {},
        }

    listed = {
        "items": [
            csr("node-agent", "system:bootstrap:abc123", "kubernetes.io/kube-apiserver-client"),
            csr("kubelet", "system:bootstrap:abc123",
                "kubernetes.io/kube-apiserver-client-kubelet"),
            csr("other-token", "system:bootstrap:zzz999", "kubernetes.io/kube-apiserver-client"),
            csr("done", "system:bootstrap:abc123", "kubernetes.io/kube-apiserver-client",
                [{"type": "Approved"}]),
        ]
    }  # fmt: skip
    approved = []
    stop = threading.Event()

    def kubectl(*args, capture=False, input=None):
        if args[:2] == ("get", "csr"):
            return subprocess.CompletedProcess(args, 0, json.dumps(listed).encode(), b"")
        approved.append(args[2])
        stop.set()
        return subprocess.CompletedProcess(args, 0, b"", b"")

    gardener_join.approve_node_agent(kubectl, stop, "abc123", every=0)
    assert approved == ["node-agent"]
