#!/usr/bin/env python3
# The Gardener release test/gardener.py provisions with (test/gardener.env):
#
#     ./tools/check-gardener.py                 # pinned vs latest
#     ./tools/check-gardener.py --json          # for update-packages.yml
#     ./tools/check-gardener.py verify          # the committed render and digest vs upstream
#     ./tools/check-gardener.py bump <version>  # move all three
#
# The code is python/src/kaetzli/updates/gardener.py, run in python/'s venv (python/README.md).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python"))
import launch  # noqa: E402

launch.module("kaetzli.updates.gardener")
