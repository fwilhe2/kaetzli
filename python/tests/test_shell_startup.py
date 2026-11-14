"""The shell startup files: /etc/bashrc exists, bash is built to read it, nothing nags."""

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASHRC = ROOT / "image/files/etc/bashrc"


def aliases(text: str) -> dict[str, str]:
    return dict(re.findall(r"^alias ([^=]+)='(.*)'$", text, re.M))


def test_bash_is_built_to_read_etc_bashrc():
    build = (ROOT / "packages/bash/build.sh").read_text()
    assert "-DSYS_BASHRC=" in build and "/etc/bashrc" in build


def test_profile_sources_bashrc_for_interactive_login_shells():
    profile = (ROOT / "image/files/etc/profile").read_text()
    assert ". /etc/bashrc" in profile


def test_default_aliases_present():
    got = aliases(BASHRC.read_text())
    for name in ("ll", "la", "..", "..."):
        assert name in got, name
    assert got["ll"] == "ls -lh"


def test_no_interactive_confirmation_aliases():
    got = aliases(BASHRC.read_text())
    for name in ("cp", "mv", "rm"):
        assert name not in got, f"{name} must not be aliased to a prompting variant"


def test_aliases_only_in_interactive_shells():
    # Sourced by a non-interactive bash, nothing may be defined.
    out = subprocess.run(
        ["bash", "-c", f". {BASHRC}; alias"], capture_output=True, text=True, check=True
    )
    assert out.stdout == ""
