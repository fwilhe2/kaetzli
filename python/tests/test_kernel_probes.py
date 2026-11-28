"""test/kernel-probes.py: the list is consistent with kernel-caps.txt, and a refused probe
is reported by its symbol, kernel-caps.txt's reason and what the command said."""

from __future__ import annotations

import re

import pytest

from kaetzli import repo
from kaetzli.boot import kernel_probes
from kaetzli.qemu import Failed

LISTED = kernel_probes.probes(repo.ROOT / kernel_probes.LIST)
WHY = kernel_probes.reasons(repo.ROOT / kernel_probes.CAPS)


def test_every_probed_symbol_is_a_required_one():
    assert [sym for sym, _ in LISTED if sym not in WHY] == []


def test_a_symbol_is_probed_once():
    syms = [sym for sym, _ in LISTED]
    assert sorted({s for s in syms if syms.count(s) > 1}) == []


def test_every_probe_has_a_command():
    assert [sym for sym, cmd in LISTED if not cmd] == []


def test_the_typed_line_cannot_match_its_own_verdict():
    line = kernel_probes.probe_line("NFT_LIMIT", "iptables -A INPUT -m limit --limit 5/min")
    assert not re.search(r"PROBE NFT_LIMIT (ok|failed)", line)
    assert "'iptables -A INPUT -m limit --limit 5/min'" in line


class FakeGuest:
    """Answers each probe as `answers` says: a symbol's output means it failed."""

    def __init__(self, answers: dict[str, str]):
        self.answers = answers
        self.sym = ""
        self.before = ""

    def mark(self) -> None:
        pass

    def send(self, line: str) -> None:
        self.sym = re.search(r'echo "PROBE (\S+) ""ok"', line).group(1)

    def wait(self, pattern: str, seconds: float) -> bool:
        said = self.answers.get(self.sym)
        self.before = "" if said is None else f"--- {self.sym}\n{said}\n"
        self.verdict = "ok" if said is None else "failed"
        return True

    def group(self) -> str:
        return self.verdict

    def fail(self, message: str) -> None:
        raise Failed(message)


def test_a_refused_probe_names_its_symbol_reason_and_error():
    said = "iptables v1.8.13 (nf_tables):  RULE_APPEND failed (No such file or directory)"
    listed = [("NFT_LIMIT", "iptables -A INPUT -m limit --limit 5/min -j RETURN"), ("DUMMY", "x")]
    failed = kernel_probes.check(FakeGuest({"NFT_LIMIT": said}), listed, WHY)
    assert failed == [
        f"CONFIG_NFT_LIMIT — {WHY['NFT_LIMIT']}\n"
        "      iptables -A INPUT -m limit --limit 5/min -j RETURN\n"
        f"      {said}"
    ]


def test_every_probe_runs_after_a_failure():
    failed = kernel_probes.check(FakeGuest({"VETH": "no", "DUMMY": "no"}), LISTED, WHY)
    assert [f.split(" ")[0] for f in failed] == ["CONFIG_VETH", "CONFIG_DUMMY"]


def test_no_verdict_fails_the_test():
    class Silent(FakeGuest):
        def wait(self, pattern, seconds):
            return False

    with pytest.raises(Failed, match="no verdict from the NF_TABLES probe"):
        kernel_probes.check(Silent({}), LISTED, WHY)
