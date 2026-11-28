"""tools/check-gardener.py, with Gardener's API, template and registry faked."""

from __future__ import annotations

import json
import shutil

import pytest

from kaetzli import repo
from kaetzli.boot import gardener as boot_gardener
from kaetzli.updates import gardener

OLD = "sha256:" + "1" * 64
NEW = "sha256:" + "2" * 64


class FakeHttp:
    def __init__(self, tag="v1.154.6", digests=None):
        self.tag = tag
        self.digests = digests or {}

    def github(self, url):
        assert url == gardener.RELEASES
        return json.dumps({"tag_name": self.tag})

    def get(self, url):
        version = url.split("/gardener/gardener/")[1].split("/")[0]
        return f"template of {version}\n"

    def header(self, url, name, accept):
        assert name == "Docker-Content-Digest" and accept == gardener.INDEX
        return self.digests[url.rsplit("/", 1)[1]]


def render(template, image):
    return f"{template}image={image}\n"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "test").mkdir()
    (tmp_path / boot_gardener.PIN).write_text(
        f'# pin\nVERSION="v1.0.0"\nNODE_AGENT_DIGEST="{OLD}"\n'
    )
    image = boot_gardener.image("v1.0.0", OLD)
    (tmp_path / boot_gardener.INIT).write_text(render("template of v1.0.0\n", image))
    return tmp_path


def test_up_to_date_is_no_update(root, capsys):
    assert gardener.main(["--json"], root, FakeHttp(tag="v1.0.0"), render) == 0
    assert capsys.readouterr().out == "[]\n"


def test_a_newer_release_is_an_update_of_its_own_kind(root, capsys):
    assert gardener.main(["--json"], root, FakeHttp(tag="v1.10.0"), render) == 0
    (entry,) = json.loads(capsys.readouterr().out)
    assert entry == {
        "package": "gardener",
        "kind": "gardener",
        "current": "v1.0.0",
        "latest": "v1.10.0",
        "url": "https://github.com/gardener/gardener/releases/tag/v1.10.0",
    }


def test_verify_passes_what_bump_wrote(root):
    http = FakeHttp(digests={"v1.0.0": OLD})
    assert gardener.main(["verify"], root, http, render) == 0


def test_verify_names_a_hand_edited_init_sh(root, capsys):
    with open(root / boot_gardener.INIT, "a") as f:
        f.write("# edited\n")
    assert gardener.main(["verify"], root, FakeHttp(digests={"v1.0.0": OLD}), render) == 1
    assert (
        "test/gardener-init.sh is not Gardener's init.tpl.sh at v1.0.0" in capsys.readouterr().err
    )


def test_verify_names_a_moved_tag(root, capsys):
    assert gardener.main(["verify"], root, FakeHttp(digests={"v1.0.0": NEW}), render) == 1
    err = capsys.readouterr().err
    assert f"NODE_AGENT_DIGEST is {OLD}, but node-agent:v1.0.0 is {NEW}" in err
    # The render is made with the registry's digest, so it is named wrong too.
    assert "test/gardener-init.sh is not" in err


def test_bump_moves_all_three_and_keeps_the_comments(root):
    http = FakeHttp(digests={"v1.1.0": NEW})
    assert gardener.main(["bump", "v1.1.0"], root, http, render) == 0
    assert boot_gardener.pinned(root) == ("v1.1.0", NEW)
    assert (root / boot_gardener.PIN).read_text().startswith("# pin\n")
    image = boot_gardener.image("v1.1.0", NEW)
    assert (root / boot_gardener.INIT).read_text() == render("template of v1.1.0\n", image)
    assert gardener.main(["verify"], root, http, render) == 0


def test_bump_refuses_what_is_not_a_tag(root):
    assert gardener.main(["bump", "1.1.0"], root, FakeHttp(), render) == 2


def test_the_committed_pin_and_render_agree():
    version, digest = boot_gardener.pinned()
    assert f'image="{boot_gardener.image(version, digest)}"' in boot_gardener.INIT_SH


@pytest.mark.skipif(not shutil.which("go"), reason="needs go")
def test_go_renders_as_text_template_does():
    template = (
        '{{ if .image -}}\nimage="{{ .image }}"\n{{- else -}}\nno\n{{- end }}\n'
        '{{- if eq .binaryName "gardener-node-agent" }}\n'
        'exec "{{ .binaryDirectory }}/{{ .binaryName }}" --config-dir="{{ .configDir }}"\n'
        "{{- end }}\n"
    )
    assert gardener.go_render(template, "img") == (
        'image="img"\n'
        'exec "/opt/bin/gardener-node-agent" --config-dir="/var/lib/gardener-node-agent"\n'
    )


def test_the_renderer_is_in_the_checkout():
    assert (repo.ROOT / "tools" / "gardener-render.go").is_file()
