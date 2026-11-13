"""tools/check-toolchains.py: builder/toolchains/ pins against upstream's latest releases.
Go's download index and Rust's stable channel manifest are injected."""

import json

import pytest

from kaetzli.updates import toolchains
from kaetzli.updates.http import FetchError

GO_INDEX = json.dumps(
    [
        {"version": "go1.28rc1", "stable": False},
        {"version": "go1.27.5", "stable": True},
        {"version": "go1.26.9", "stable": True},
    ]
)
RUST_CHANNEL = """\
manifest-version = "2"
date = "2026-11-12"

[pkg.cargo]
version = "0.101.0 (3acc79c0c 2026-11-05)"

[pkg.rustc]
version = "1.101.0 (0ef1bc386 2026-11-09)"
git_commit_hash = "0ef1bc386059596dada8fb65b50425aab2ea637a"
"""


class Upstream:
    def __init__(self, go=GO_INDEX, rust=RUST_CHANNEL):
        self.bodies = {toolchains.GO_INDEX: go, toolchains.RUST_CHANNEL: rust}

    def get(self, url):
        body = self.bodies[url]
        if body is None:
            raise FetchError(f"{url}: 503")
        return body


@pytest.fixture
def tree(tmp_path):
    d = tmp_path / "builder" / "toolchains"
    d.mkdir(parents=True)
    (d / "go.sh").write_text('# Go\nVERSION="1.27.4"\nCOMPONENTS="go"\n')
    (d / "rust.sh").write_text('VERSION="1.101.0"\n')
    (d / "go-key.asc").write_text("not a pin file\n")
    return tmp_path


def run(tree, argv, http, capsys):
    rc = toolchains.check(argv, tree, http)
    out, err = capsys.readouterr()
    return rc, out, err


def test_latest_go_skips_unstable():
    assert toolchains.latest_go(Upstream()) == "1.27.5"


def test_latest_rust_is_rustc_not_cargo():
    assert toolchains.latest_rust(Upstream()) == "1.101.0"


def test_pinned_reads_through_bash(tree):
    assert toolchains.pinned(tree, "go") == "1.27.4"
    assert toolchains.names(tree) == ["go", "rust"]


def test_report(tree, capsys):
    url = "https://go.dev/doc/devel/release#go1.27.5"
    assert run(tree, [], Upstream(), capsys) == (
        0,
        f"go           1.27.4     -> 1.27.5     {url}\nrust         1.101.0    up to date\n",
        "",
    )


def test_json_is_one_compact_line_per_run(tree, capsys):
    rc, out, _ = run(tree, ["--json"], Upstream(), capsys)
    assert rc == 0
    assert out == (
        '[{"package":"go","kind":"toolchain","current":"1.27.4","latest":"1.27.5",'
        '"url":"https://go.dev/doc/devel/release#go1.27.5"}]\n'
    )


def test_up_to_date_json(tree, capsys):
    (tree / "builder" / "toolchains" / "go.sh").write_text('VERSION="1.27.5"\n')
    assert run(tree, ["--json"], Upstream(), capsys) == (0, "[]\n", "")


def test_never_a_downgrade(tree, capsys):
    (tree / "builder" / "toolchains" / "rust.sh").write_text('VERSION="1.102.0"\n')
    rc, out, _ = run(tree, ["rust"], Upstream(), capsys)
    assert (rc, out) == (0, "rust         1.102.0    up to date\n")


def test_only_the_named(tree, capsys):
    rc, out, _ = run(tree, ["rust"], Upstream(), capsys)
    assert (rc, out) == (0, "rust         1.101.0    up to date\n")


def test_unknown_toolchain(tree, capsys):
    rc, out, err = run(tree, ["zig"], Upstream(), capsys)
    assert (rc, out) == (2, "")
    assert err == "error: unknown toolchain 'zig' (no builder/toolchains/zig.sh)\n"


@pytest.mark.parametrize(
    "http",
    [Upstream(go=None), Upstream(go="not json"), Upstream(go="[]")],
)
def test_an_unreachable_upstream_fails_but_the_others_report(tree, capsys, http):
    rc, out, err = run(tree, ["--json"], http, capsys)
    assert rc == 1
    assert out == "[]\n"
    assert err == "error: go: could not read the latest release from upstream\n"
