"""packages/user-data/src/user-data against a local metadata service, and the stdio
responder test/user-data.py hands qemu (kaetzli.boot.user_data.serve)."""

from __future__ import annotations

import functools
import io
import os
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from kaetzli.boot import user_data

SCRIPT = Path(__file__).resolve().parents[2] / "packages" / "user-data" / "src" / "user-data"


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def cloud(tmp_path):
    """A metadata service serving tmp_path/srv, and the script's directories."""
    srv = tmp_path / "srv"
    user_data.metadata_dir(srv)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(srv)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for d in ("state", "run"):
        (tmp_path / d).mkdir()
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("root=/dev/dm-0 ro kaetzli.user-data=openstack quiet\n")
    yield tmp_path, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def run_script(tmp: Path, base: str, user_data_text: str | None = None, cmdline=None):
    latest = tmp / "srv" / "openstack" / "latest"
    if user_data_text is not None:
        (latest / "user_data").write_text(user_data_text)
    env = {
        "PATH": "/usr/bin:/bin",
        "STATE_DIRECTORY": str(tmp / "state"),
        "RUNTIME_DIRECTORY": str(tmp / "run"),
        "USER_DATA_BASE": base,
        "USER_DATA_CMDLINE": str(cmdline or tmp / "cmdline"),
        "OUT": str(tmp / "ran"),
    }
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


COUNTING = '#!/bin/bash\necho ran >> "$OUT"\n'


def test_runs_the_user_data_once_per_instance(cloud):
    tmp, base = cloud
    first = run_script(tmp, base, COUNTING)
    assert first.returncode == 0, first.stdout + first.stderr
    assert (tmp / "state" / "done").read_text() == user_data.UUID + "\n"
    again = run_script(tmp, base)
    assert again.returncode == 0
    assert "already ran" in again.stdout
    assert (tmp / "ran").read_text() == "ran\n"


def test_a_new_instance_runs_it_again(cloud):
    tmp, base = cloud
    (tmp / "state" / "done").write_text("another-instance\n")
    assert run_script(tmp, base, COUNTING).returncode == 0
    assert (tmp / "ran").read_text() == "ran\n"


def test_a_failing_script_records_nothing(cloud):
    tmp, base = cloud
    result = run_script(tmp, base, "#!/bin/bash\nexit 3\n")
    assert result.returncode == 3
    assert not (tmp / "state" / "done").exists()


def test_no_user_data_is_not_an_error(cloud):
    tmp, base = cloud
    (tmp / "srv" / "openstack" / "latest" / "user_data").unlink()
    result = run_script(tmp, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no user data" in result.stdout
    assert (tmp / "state" / "done").read_text() == user_data.UUID + "\n"


def test_cloud_config_is_logged_and_not_run(cloud):
    tmp, base = cloud
    result = run_script(tmp, base, "#cloud-config\nruncmd: [touch, /x]\n")
    assert result.returncode == 0
    assert "<4>user data is not a script" in result.stdout
    assert "#cloud-config" in result.stdout


def test_an_unknown_source_fails(cloud):
    tmp, base = cloud
    cmdline = tmp / "cmdline-ec2"
    cmdline.write_text("kaetzli.user-data=ec2\n")
    result = run_script(tmp, base, COUNTING, cmdline=cmdline)
    assert result.returncode == 1
    assert "unknown source" in result.stdout
    assert not (tmp / "ran").exists()


def test_metadata_without_a_uuid_fails(cloud):
    tmp, base = cloud
    (tmp / "srv" / "openstack" / "latest" / "meta_data.json").write_text('{"name": "x"}')
    result = run_script(tmp, base, COUNTING)
    assert result.returncode == 1
    assert "no instance uuid" in result.stdout


def test_the_responder_serves_files_and_logs_requests(tmp_path):
    user_data.metadata_dir(tmp_path)
    out = io.BytesIO()
    user_data.serve(
        tmp_path,
        io.BytesIO(b"GET /openstack/latest/user_data HTTP/1.1\r\nHost: x\r\n\r\n"),
        out,
    )
    head, body = out.getvalue().split(b"\r\n\r\n", 1)
    assert head.startswith(b"HTTP/1.1 200 OK")
    assert body.decode() == user_data.USER_DATA
    assert (tmp_path / "requests.log").read_text() == "/openstack/latest/user_data\n"


def test_the_responder_stays_inside_its_directory(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret").write_text("no")
    out = io.BytesIO()
    user_data.serve(root, io.BytesIO(b"GET /../secret HTTP/1.1\r\n\r\n"), out)
    assert out.getvalue().startswith(b"HTTP/1.1 404")


def test_the_relay_carries_one_connection_each_way():
    """What a guestfwd -cmd: relay does for test/gardener-join.py: bytes in on stdin go
    to the host port, what comes back goes out on stdout, until the guest closes."""
    import socket
    import threading

    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]

    def echo():
        conn, _ = server.accept()
        with conn:
            while data := conn.recv(1024):
                conn.sendall(data.upper())

    threading.Thread(target=echo, daemon=True).start()
    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    with open(in_r, "rb", buffering=0) as stdin, open(out_w, "wb", buffering=0) as stdout:
        relay = threading.Thread(target=user_data.relay, args=("127.0.0.1", port, stdin, stdout))
        relay.start()
        os.write(in_w, b"hello")
        assert os.read(out_r, 5) == b"HELLO"
        os.close(in_w)
        relay.join(5)
    assert not relay.is_alive()
    os.close(out_r)
    server.close()
