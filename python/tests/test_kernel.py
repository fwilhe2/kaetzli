"""test/kernel-caps.py, kernel-parity.py and kernel-config-diff.py.

fixtures/kernel/configs/ are CI's resolved kernel configs (7.3.1 for both arches, and
amd64's 7.2.13 before the bump), and two broken copies; kconfig/ is a small tree with
one Kconfig entry for each way kernel-config-diff classifies a symbol, and diff/ two
configs that move each of them. expected/ is what the bash versions printed and
returned. Deliberate differences, edited into the goldens:

- kernel-config-diff lists changes by symbol, a removal before an addition, where the
  bash version had GNU diff's hunk order (every removal in a hunk, then every addition,
  so CONFIG_VALUE's old and new values could be lines apart).
- kernel-parity's failure names test/kernel-parity-allowed.txt, where the list now is,
  rather than the script; kernel-caps' list is test/kernel-caps.txt.
- A usage error exits 2, with the usage on stderr.
"""

import gzip
import subprocess
from pathlib import Path

import pytest

from kaetzli import repo
from kaetzli.kernel import caps, config, config_diff, hardening, parity

FIXTURES = Path(__file__).parent / "fixtures" / "kernel"
EXPECTED = FIXTURES / "expected"
KCONFIG = FIXTURES / "kconfig"


@pytest.fixture(scope="module")
def trees(tmp_path_factory) -> Path:
    """Each fixture config as a tree with boot/config, as the rootfs artifact has it."""
    base = tmp_path_factory.mktemp("kernels")
    for gz in (FIXTURES / "configs").glob("*.config.gz"):
        boot = base / gz.name.removesuffix(".config.gz") / "boot"
        boot.mkdir(parents=True)
        (boot / "config").write_bytes(gzip.decompress(gz.read_bytes()))
    return base


GOLDEN = {
    "caps-amd64": (caps, ["{t}/amd64-7.3.1"]),
    "caps-arm64": (caps, ["{t}/arm64-7.3.1"]),
    "caps-amd64-broken": (caps, ["{t}/amd64-broken"]),
    "caps-arm64-broken": (caps, ["{t}/arm64-broken"]),
    "caps-no-config": (caps, ["nowhere"]),
    "parity": (parity, ["{t}/amd64-7.3.1", "{t}/arm64-7.3.1"]),
    "parity-across-versions": (parity, ["{t}/amd64-7.2.13/boot/config", "{t}/arm64-7.3.1"]),
    "diff-bump": (
        config_diff,
        ["{t}/amd64-7.2.13/boot/config", "{t}/amd64-7.3.1/boot/config"],
    ),
    "diff-same": (
        config_diff,
        ["{t}/amd64-7.3.1/boot/config", "{t}/amd64-7.3.1/boot/config"],
    ),
    "diff-kconfig": (config_diff, ["{f}/diff/old.config", "{f}/diff/new.config", "{f}/kconfig"]),
    "diff-no-kconfig": (config_diff, ["{f}/diff/old.config", "{f}/diff/new.config"]),
}


def test_every_golden_is_run():
    assert set(GOLDEN) == {p.name for p in EXPECTED.iterdir()}


@pytest.mark.parametrize("case", sorted(GOLDEN))
def test_the_output_is_the_bash_versions(case, trees, capsys, monkeypatch):
    module, args = GOLDEN[case]
    monkeypatch.chdir("/")  # the scripts cd to the root themselves
    rc = module.main([a.format(t=trees, f=FIXTURES) for a in args])
    out, err = capsys.readouterr()
    expected = EXPECTED / case
    assert (out, err, rc) == (
        (expected / "stdout.txt").read_text(),
        (expected / "stderr.txt").read_text(),
        int((expected / "rc").read_text()),
    )


class TestConfig:
    def test_a_tree_or_a_file(self, tmp_path):
        (tmp_path / "boot").mkdir()
        assert config.path_of(str(tmp_path)) == tmp_path / "boot" / "config"
        assert config.path_of("x/.config") == Path("x/.config")

    def test_values_set_and_not_set(self, tmp_path):
        p = tmp_path / "c"
        p.write_text(
            "# Linux/arm64 7.3.1 Kernel Configuration\n"
            "CONFIG_A=y\n"
            'CONFIG_S="a b=c"\n'
            "# CONFIG_B is not set\n"
            "# a comment\n"
            "CONFIG_lower=y\n"
        )
        assert config.values(p) == {"CONFIG_A": "y", "CONFIG_S": '"a b=c"', "CONFIG_B": "n"}
        assert config.settings(p) == {"CONFIG_A=y", 'CONFIG_S="a b=c"'}
        assert config.arch(p) == "arm64"

    def test_symbol_list_with_sections(self, tmp_path):
        p = tmp_path / "list"
        p.write_text("# a comment\n\nA    what  breaks\n[x86]\nB  only x86 \n\n[arm64]\nC c\n")
        assert config.symbol_list(p) == [
            (None, "A", "what  breaks"),
            ("x86", "B", "only x86"),
            ("arm64", "C", "c"),
        ]


class TestCaps:
    def test_the_real_list_has_both_arches(self):
        entries = config.symbol_list(repo.ROOT / caps.LIST)
        sections = {s for s, _, _ in entries}
        assert sections == {None, "x86", "arm64"}
        assert all(why for _, _, why in entries)

    def test_required_follows_the_arch(self):
        entries = [(None, "A", "a"), ("x86", "B", "b"), ("arm64", "C", "c")]
        assert [s for s, _ in caps.required(entries, "x86")] == ["A", "B"]
        assert [s for s, _ in caps.required(entries, "arm64")] == ["A", "C"]
        assert [s for s, _ in caps.required(entries, None)] == ["A"]

    def test_only_an_exact_y_counts(self, tmp_path):
        boot = tmp_path / "boot"
        boot.mkdir()
        (boot / "config").write_text("CONFIG_A=m\nCONFIG_B=y \nCONFIG_C=y\n")
        missing = caps.missing(
            boot / "config", [(None, "A", "a"), (None, "B", "b"), (None, "C", "c")]
        )
        assert missing == ["CONFIG_A — a", "CONFIG_B — b"]

    def test_the_default_is_rootfs(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(repo, "enter_root", lambda: monkeypatch.chdir(tmp_path))
        assert caps.main([]) == 1
        assert "no kernel config at rootfs/boot/config" in capsys.readouterr().err


class TestHardening:
    def test_the_real_list_has_both_arches_and_both_kinds(self):
        entries = config.symbol_list(repo.ROOT / hardening.LIST)
        assert {s for s, _, _ in entries} == {None, "x86", "arm64"}
        assert any(sym.startswith("!") for _, sym, _ in entries)
        assert all(why for _, _, why in entries)

    def test_a_negated_symbol_must_not_be_y(self, tmp_path):
        p = tmp_path / "config"
        p.write_text("CONFIG_ON=y\nCONFIG_BAD=y\n# CONFIG_OFF is not set\n")
        entries = [(None, "ON", "on"), (None, "!BAD", "bad"), (None, "!OFF", "off")]
        entries.append((None, "!ABSENT", "an arch without the symbol"))
        assert caps.missing(p, entries) == ["CONFIG_BAD is set — bad"]

    def test_the_fixtures_predate_it(self, trees, capsys, monkeypatch):
        # CI's 7.3.1 configs were built before kspp.config had lockdown: the check fails
        # them, naming it.
        monkeypatch.chdir("/")
        assert hardening.main([str(trees / "amd64-7.3.1")]) == 1
        err = capsys.readouterr().err
        assert "CONFIG_SECURITY_LOCKDOWN_LSM —" in err
        assert "CONFIG_DEVMEM is set" in err

    def test_a_hardened_config_passes(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir("/")
        entries = config.symbol_list(repo.ROOT / hardening.LIST)
        boot = tmp_path / "boot"
        boot.mkdir()
        on = [f"CONFIG_{sym}=y" for s, sym, _ in entries if s in (None, "x86") and sym[0] != "!"]
        (boot / "config").write_text("# Linux/x86 0.0.0 Kernel Configuration\n" + "\n".join(on))
        assert hardening.main([str(tmp_path)]) == 0
        assert "kernel hardening OK" in capsys.readouterr().out
        script = repo.ROOT / "test" / "kernel-hardening.py"
        r = subprocess.run([str(script), str(tmp_path)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def test_usage(self, capsys):
        assert hardening.main(["a", "b"]) == 2
        assert "usage: test/kernel-hardening.py" in capsys.readouterr().err


class TestParity:
    def test_the_real_allowlist(self):
        allowed = parity.allowed(repo.ROOT / parity.LIST)
        assert "CONFIG_OF" in allowed and len(allowed) == 39

    def test_only_symbols_both_define_and_not_allowed(self):
        a = {"CONFIG_A": "y", "CONFIG_B": "n", "CONFIG_X86": "y", "CONFIG_OF": "n"}
        b = {"CONFIG_A": "y", "CONFIG_B": "y", "CONFIG_ARM": "y", "CONFIG_OF": "y"}
        assert parity.differences(a, b, {"CONFIG_OF"}) == [("CONFIG_B", "n", "y")]

    def test_differences_in_byte_order(self):
        a = {"CONFIG_B": "1", "CONFIG_A_B": "1", "CONFIG_A": "1"}
        b = {k: "2" for k in a}
        assert [d[0] for d in parity.differences(a, b, set())] == [
            "CONFIG_A",
            "CONFIG_A_B",
            "CONFIG_B",
        ]

    def test_usage(self, capsys):
        assert parity.main(["one"]) == 2
        assert "usage:" in capsys.readouterr().err


@pytest.fixture(scope="module")
def classes():
    return config_diff.classify(KCONFIG)


class TestClassify:
    @pytest.mark.parametrize(
        ("symbol", "prompted", "toolchain"),
        [
            ("PROMPTED", True, False),
            ("DERIVED", False, False),
            ("CC_HAS_THING", False, True),  # $( in its default
            ("GCC_VERSION", False, True),
            ("CC_HAS_COUNTED_BY", False, True),  # depends on a toolchain symbol
            ("USES_COUNTED", False, True),  # and one more step out
            ("PROMPTED_TOOLCHAIN", True, False),  # a prompt is always worth seeing
            ("HELP_QUIRK", False, True),  # help text is read as a default, as before
            ("VALUE", True, False),
            ("MENU_THING", True, False),  # menuconfig, in a file under sub/
            ("TWICE", True, False),  # prompted in one of its two definitions
        ],
    )
    def test_symbol(self, classes, symbol, prompted, toolchain):
        assert classes[f"CONFIG_{symbol}"] == (prompted, toolchain)

    def test_documentation_is_not_read(self, classes):
        assert "CONFIG_DOC_ONLY" not in classes

    def test_report_without_classes_lists_everything(self):
        old = {"CONFIG_A=y", "CONFIG_V=1"}
        new = {"CONFIG_V=2", "CONFIG_B=y"}
        assert config_diff.report(old, new, {}) == [
            "- CONFIG_A=y",
            "+ CONFIG_B=y",
            "- CONFIG_V=1",
            "+ CONFIG_V=2",
        ]

    def test_report_of_the_same_config_is_empty(self):
        assert config_diff.report({"CONFIG_A=y"}, {"CONFIG_A=y"}, {}) == []

    def test_usage(self, capsys):
        assert config_diff.main(["one"]) == 2
        assert "usage:" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("script", "args"),
    [
        ("kernel-caps.py", ["{t}/amd64-7.3.1"]),
        ("kernel-parity.py", ["{t}/amd64-7.3.1", "{t}/arm64-7.3.1"]),
        ("kernel-config-diff.py", ["{t}/amd64-7.3.1/boot/config"] * 2),
    ],
)
def test_the_entry_points(script, args, trees, tmp_path):
    r = subprocess.run(
        [str(repo.ROOT / "test" / script), *(a.format(t=trees) for a in args)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
