"""kaetzli.boot.gardener: the user data an OS extension would generate for a Gardener
worker, which test/gardener.py serves to a kaetzli VM. These check the script itself;
the boot test checks what it does."""

from __future__ import annotations

import base64
import re
import subprocess

import pytest

from kaetzli import repo
from kaetzli.boot import gardener


def written(script: str) -> dict[str, tuple[bytes, str | None]]:
    """{path: (content, mode)} for every `cat << EOF ... > path` the script has, decoded
    as bash would (base64 -d where the line says so)."""
    files: dict[str, tuple[bytes, str | None]] = {}
    pat = re.compile(
        r'cat << EOF (\| base64 -d )?> "([^"]+)"\n(.*?)\nEOF(?:\nchmod "(\d+)" "\2")?', re.S
    )
    for m in pat.finditer(script):
        body = m[3].encode()
        files[m[2]] = (base64.b64decode(body) if m[1] else body, m[4])
    return files


def test_the_script_is_bash_and_parses():
    script = gardener.user_data()
    assert script.startswith("#!/bin/bash\n")
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)


def test_it_runs_once_as_gardeners_wrapper_makes_it():
    script = gardener.user_data()
    assert 'if [ -f "/var/lib/osc/provision-osc-applied" ]; then' in script
    assert script.rstrip().endswith("touch /var/lib/osc/provision-osc-applied")


def test_the_files_are_where_node_agent_looks_with_gardeners_modes():
    files = written(gardener.user_data())
    base = "/var/lib/gardener-node-agent"
    assert files[f"{base}/init.sh"] == (gardener.INIT_SH.encode(), "0755")
    assert files[f"{base}/cluster-ca.crt"][1] == "0640"
    assert files[f"{base}/credentials/bootstrap-token"][1] == "0640"
    assert files[f"{base}/machine-name"][1] == "0640"
    # node-agent reads config-<its own version>.yaml, so the name follows the image's tag
    config, mode = files[f"{base}/config-{gardener.VERSION}.yaml"]
    assert mode == "0600"
    assert b"kind: NodeAgentConfiguration" in config
    assert f"kubernetesVersion: {gardener.kubelet_version()}".encode() in config


def test_the_init_unit_is_gardeners_and_is_started_without_waiting():
    script = gardener.user_data()
    unit, _ = written(script)["/etc/systemd/system/gardener-node-init.service"]
    assert b"Requires=containerd.service" in unit
    assert b"ExecStart=/var/lib/gardener-node-agent/init.sh" in unit
    assert b"EnvironmentFile=/etc/environment" in unit
    assert (
        "systemctl enable 'gardener-node-init.service' && "
        "systemctl restart --no-block 'gardener-node-init.service'"
    ) in script
    assert script.index("systemctl daemon-reload") < script.index("systemctl enable")


def test_init_sh_pulls_the_pinned_image_by_digest():
    assert f'image="{gardener.IMAGE}"' in gardener.INIT_SH
    assert re.fullmatch(
        r"europe-docker\.pkg\.dev/gardener-project/releases/gardener/node-agent:"
        r"v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}",
        gardener.IMAGE,
    )
    assert gardener.IMAGE.split(":")[1].split("@")[0] == gardener.VERSION


def test_the_kubernetes_version_is_the_extensions_kubelet():
    (pinned,) = repo.env_get("kubernetes", "VERSION")
    assert gardener.kubelet_version() == pinned


@pytest.mark.parametrize("check", [gardener.CHECK, gardener.SECOND_CHECK])
def test_each_check_is_one_complete_bash_command(check):
    """A quote in a failure message leaves bash waiting for the rest of the line, and the
    test then times out rather than saying anything."""
    assert subprocess.run(["bash", "-n", "-c", check]).returncode == 0
    for what, _ in gardener.CHECKS + gardener.SECOND_CHECKS:
        assert "'" not in what, what
