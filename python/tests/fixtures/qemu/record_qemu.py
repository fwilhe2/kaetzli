"""A stand-in for qemu in python/tests/test_k8s_demo.py: it writes its arguments,
NUL-separated, to <pidfile>.argv, binds the unix sockets of the stream netdevs it serves
(as the router's qemu does, which the nodes connect to), and exits."""

import socket
import sys
from pathlib import Path

args = sys.argv[1:]
pidfile = args[args.index("-pidfile") + 1]
Path(f"{pidfile}.argv").write_bytes(b"".join(a.encode() + b"\0" for a in args))
for a in args:
    if a.startswith("stream,") and "server=on" in a:
        path = dict(kv.split("=", 1) for kv in a.split(",") if "=" in kv)["addr.path"]
        socket.socket(socket.AF_UNIX).bind(path)
