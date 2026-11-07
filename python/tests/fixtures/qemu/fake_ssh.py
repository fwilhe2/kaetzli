"""A stand-in for the host's ssh client in python/tests/test_boot_golden.py, for
tools/ssh.sh's `ssh -F config -l <login> kaetzli ...`: it answers as argv[1]'s JSON says
for that login, {"root": [stdout, rc], ...}, and refuses a login it does not name."""

import json
import sys
from pathlib import Path

answers = json.loads(Path(sys.argv[1]).read_text())
args = sys.argv[2:]
login = args[args.index("-l") + 1] if "-l" in args else ""
out, rc = answers.get(login, ["Permission denied (publickey).\n", 255])
(sys.stdout if rc == 0 else sys.stderr).write(out)
sys.exit(rc)
