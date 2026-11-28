"""image/files/etc/systemd/system/systemd-confext.service.d/50-mutable-etc.conf: after the
merge it starts again the targets the boot still has a start job for, so a unit enabled
into /etc's upper layer starts (test/gardener.py's second boot). Run here as systemd would
run it, against a fake systemctl."""

from __future__ import annotations

import re
import subprocess

from kaetzli import repo

DROPIN = repo.ROOT / "image/files/etc/systemd/system/systemd-confext.service.d/50-mutable-etc.conf"

# The boot's jobs at the merge on a slow (emulated) arm64 boot: network.target still
# queued, and it refuses a manual start (RefuseManualStart=yes).
JOBS = """\
 12 multi-user.target    start waiting
 13 network.target       start waiting
 14 graphical.target     start waiting
 15 systemd-networkd.service start running
 16 basic.target         stop  waiting
"""


def command() -> str:
    """ExecStartPost='s bash -c script, unquoted as systemd unquotes it: \\" is ", $$ is $."""
    (line,) = [x for x in DROPIN.read_text().splitlines() if x.startswith("ExecStartPost=")]
    script = re.fullmatch(r'ExecStartPost=-/usr/bin/bash -c "(.*)"', line).group(1)
    return script.replace('\\"', '"').replace("$$", "$")


def run_with(tmp_path) -> tuple[int, list[str], str]:
    log = tmp_path / "started"
    fake = tmp_path / "systemctl"
    fake.write_text(
        "#!/bin/bash\n"
        f'case "$1" in\n'
        f"  list-jobs) cat <<'EOF'\n{JOBS}EOF\n ;;\n"
        "  show) [[ $4 == network.target ]] && echo yes || echo no ;;\n"
        "  --no-block) shift 2; for u; do [[ $u == network.target ]] && "
        '{ echo "Operation refused, unit $u may be requested by dependency only" >&2; exit 1; }; '
        f'echo "$u" >> {log}; done ;;\n'
        "esac\n"
    )
    fake.chmod(0o755)
    p = subprocess.run(
        ["bash", "-c", command()],
        env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    return p.returncode, (log.read_text().split() if log.exists() else []), p.stderr


def test_the_queued_targets_are_started_again(tmp_path):
    _, started, _ = run_with(tmp_path)
    assert sorted(started) == ["graphical.target", "multi-user.target"]


def test_a_target_that_refuses_a_manual_start_is_left_alone(tmp_path):
    """systemctl logs the refusal at err, which test/systemd.py fails the boot on."""
    rc, started, stderr = run_with(tmp_path)
    assert "network.target" not in started
    assert stderr == ""
    assert rc == 0
