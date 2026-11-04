"""How long the image takes to boot: what test/systemd.py records (kaetzli.boot.timing),
and test/boot-time-history.py, which puts it beside the last builds on main."""

import io
import json

import pytest
from test_gh import FakeGh

from kaetzli import report
from kaetzli.boot import timing
from kaetzli.gh import Gh


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("412ms", 0.412),
        ("1.892s", 1.892),
        ("1min 2.500s", 62.5),
        ("1h 2min 3s", 3723.0),
        ("850us", 0.00085),
        ("2.304s ", 2.304),
    ],
)
def test_timespans_are_systemds(text, seconds):
    assert timing.seconds(text) == pytest.approx(seconds)


def test_a_timespan_that_is_not_one_is_refused():
    with pytest.raises(ValueError):
        timing.seconds("soon")


def test_systemd_analyze_time_is_read_phase_by_phase():
    line = "Startup finished in 412ms (kernel) + 1min 1.892s (userspace) = 1min 2.304s"
    phases, total = timing.parse_time(line)
    assert phases == pytest.approx({"kernel": 0.412, "userspace": 61.892})
    assert total == pytest.approx(62.304)


def test_firmware_and_loader_are_phases_too():
    line = (
        "Startup finished in 2.1s (firmware) + 1s (loader) + 400ms (kernel) + 1s (userspace) = 4.5s"
    )
    phases, total = timing.parse_time(line)
    assert list(phases) == ["firmware", "loader", "kernel", "userspace"]
    assert total == pytest.approx(4.5)


def test_a_boot_not_finished_is_none():
    assert timing.parse_time("Bootup is not yet finished (...). Please try again later.") is None


def test_blame_is_unit_and_time():
    lines = ["1.234s systemd-networkd-wait-online.service", "1min 2s slow.service", "junk"]
    assert timing.parse_blame(lines) == [
        ("systemd-networkd-wait-online.service", 1.234),
        ("slow.service", 62.0),
    ]


def test_the_record_reads_back(tmp_path):
    b = timing.BootTime("kvm", {"kernel": 0.4, "userspace": 1.9}, 2.3, [("a.service", 0.8)])
    p = tmp_path / "boot-time.txt"
    timing.write(b, p)
    assert timing.read(p) == b


def _record(accel, kernel, userspace, blame=()):
    return timing.BootTime(
        accel, {"kernel": kernel, "userspace": userspace}, kernel + userspace, list(blame)
    )


def _gh(builds: dict[str, timing.BootTime | None]):
    """gh answering with one green run per entry, newest first, each with its record."""
    runs = [
        {
            "databaseId": int(i),
            "headSha": f"{i:0<40}",
            "createdAt": f"2026-11-0{len(builds) - n}T10:00:00Z",
        }
        for n, i in enumerate(builds)
    ]
    downloads = {}
    for i, b in builds.items():
        if b is not None:
            downloads[(i, "boot-time-amd64")] = {"boot-time.txt": timing.render_record(b)}
    return FakeGh(
        [(["gh", "auth", "status"], (0, "")), (["gh", "run", "list"], (0, json.dumps(runs)))],
        downloads,
    )


def _history(current, builds):
    out = io.StringIO()
    timing.history(
        Gh(run=_gh(builds)), "amd64", 12, current, report.Report(stdout=out), here="f" * 40
    )
    return out.getvalue()


def test_the_history_compares_like_with_like():
    current = _record("kvm", 0.5, 2.0, [("systemd-networkd.service", 0.3)])
    builds = {
        "3": _record("kvm", 0.4, 1.5, [("systemd-networkd.service", 0.1)]),
        "2": _record("tcg", 4.0, 20.0),
        "1": _record("kvm", 0.4, 1.4),
    }
    out = _history(current, builds)
    assert "## Boot time — amd64" in out
    # Two kvm builds and this one; the tcg one is left out of the series, and said so.
    assert "| userspace |" in out
    assert "1.50 s | **2.00 s** | +0.50 s |" in out
    assert "1 build under tcg left out" in out
    assert "| `systemd-networkd.service` | 0.30 s | +0.20 s |" in out


def test_the_history_without_builds_is_this_one_and_a_note():
    out = io.StringIO()
    with pytest.raises(report.Note, match="no boot-time artifacts"):
        timing.history(
            Gh(run=_gh({})), "amd64", 12, _record("kvm", 0.5, 2.0), report.Report(stdout=out),
            here="f" * 40,
        )  # fmt: skip
    assert "**2.50 s**" in out.getvalue()


def test_the_history_without_gh_is_a_note():
    out = io.StringIO()
    with pytest.raises(report.Note, match="no `gh`"):
        timing.history(
            Gh(which=lambda _: None), "amd64", 12, _record("kvm", 0.5, 2.0),
            report.Report(stdout=out), here="f" * 40,
        )  # fmt: skip


def test_main_without_a_record_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(timing, "RECORD", str(tmp_path / "boot-time.txt"))
    assert report.run(timing.main, ["amd64"]) == 0
    assert "no boot time" in capsys.readouterr().err
