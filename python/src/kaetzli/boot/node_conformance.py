"""test/node-conformance.py: Kubernetes node conformance, the image and its kubernetes
extension held to the [NodeConformance] specs of upstream's node e2e suite.

    ./test/node-conformance.py [rootfs.erofs] [bzImage]
    ./test/node-conformance.py output/boot-image/rootfs.erofs output/boot-image/bzImage   # CI's

The suite is Kubernetes' own e2e_node.test, built by tools/build-node-e2e.sh from the
tarball packages/kubernetes pins, and driven by ginkgo inside the guest. It needs no
cluster: it runs etcd and kube-apiserver linked into itself, and starts the node's
kubelet (the extension's /usr/bin/kubelet) through systemd-run against the extension's
CRI-O. This is how upstream's node e2e jobs run it, and what replaced the containerized
node-test image the Kubernetes documentation still describes, which expects the API
server's long-gone insecure port.

One node, booted like a tools/k8s-demo.py node: the kubernetes extension attached, a
/var disk, user-mode networking for the images the specs pull from registry.k8s.io.
Before the suite, the guest gets what upstream's test nodes have and a real node would
get from its cluster, all of it under /run or /var, so nothing the image ships is
changed:

  - the extension's kubelet.service masked, since the suite starts a kubelet of its own
  - a bridge CNI config, as a cluster's CNI DaemonSet would write it
  - a CRI-O runtime handler named test-handler, which the RuntimeClass and PodOverhead
    specs expect every test node to define (crun, under another name)

Failures are listed by spec name at the end, and in CI also as error annotations and in
the job summary. The full ginkgo output is in
output/node-conformance/run.log, and the suite's own results (junit, kubelet.log,
services.log for etcd and the API server, crio.log) in output/node-conformance/results/.
The guest's console is output/node-conformance-console.log.

Overrides, from the environment:
  FOCUS, SKIP     ginkgo regexes (default \\[NodeConformance\\], minus Flaky, Slow and
                  Serial: what upstream's pull-request job runs)
  PROCS           parallel ginkgo processes (default CPUS)
  MEM, CPUS       the guest (default 6144 MB, 4)
  TIMEOUT         seconds to wait for ssh (default 300)
  SUITE_TIMEOUT   ginkgo's limit for the whole run (default 2h)
  KEEP=1          leave the guest running afterwards, to look around (it says how)

It needs KVM: under emulation a run would take most of a day. Without a writable
/dev/kvm it skips, with a note, as test/k8s.py does.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from kaetzli import repo
from kaetzli.qemu import Machine, Refused, refuse
from kaetzli.qemu.machine import kvm_usable

STATE = Path("output/node-conformance")
LOG = Path("output/node-conformance-console.log")

# What the guest gets before the suite, as one script on ssh's stdin.
FIXTURES = r"""set -euo pipefail
systemctl mask --runtime --now kubelet.service

cat > /etc/cni/net.d/10-node-e2e.conflist <<'EOF'
{
  "cniVersion": "1.0.0",
  "name": "node-e2e",
  "plugins": [
    {
      "type": "bridge", "bridge": "cni0", "isGateway": true, "ipMasq": true, "hairpinMode": true,
      "ipam": {"type": "host-local", "routes": [{"dst": "0.0.0.0/0"}], "ranges": [[{"subnet": "10.85.0.0/16"}]]}
    },
    {"type": "portmap", "capabilities": {"portMappings": true}}
  ]
}
EOF

# CRI-O reads one drop-in directory, and the confext's is read-only: a copy of it in /run,
# plus the handler, named on CRI-O's command line for this boot only.
mkdir -p /run/node-e2e/crio.conf.d /run/systemd/system/crio.service.d
cp /etc/crio/crio.conf.d/* /run/node-e2e/crio.conf.d/
cat > /run/node-e2e/crio.conf.d/90-test-handler.conf <<'EOF'
[crio.runtime.runtimes.test-handler]
runtime_path = "/usr/bin/crun"
runtime_root = "/run/crun"
EOF
printf '[Service]\nExecStart=\nExecStart=/usr/bin/crio --config-dir=/run/node-e2e/crio.conf.d\n' \
    > /run/systemd/system/crio.service.d/90-node-e2e.conf
systemctl daemon-reload
systemctl restart crio
crictl info > /dev/null
mkdir -p /var/lib/node-e2e
# The suite looks for the kubelet next to itself.
ln -sfn /usr/bin/kubelet /var/lib/node-e2e/kubelet
"""  # noqa: E501 (the CNI config's lines are the bash version's)

_RAN = re.compile(r"^Ran [0-9]* of [0-9]* Specs")
_COUNTS = re.compile(r"^(SUCCESS|FAIL)! -- ")
_SUMMARY = re.compile(r"^Summarizing [0-9]* Failure")
_FAILED = re.compile(r"^  \[(FAIL|TIMEDOUT|PANICKED|INTERRUPTED)\] ")


@dataclass
class Verdict:
    ran: str  # ginkgo's "Ran N of M Specs" line, or ""
    counts: str  # its "SUCCESS! -- …" or "FAIL! -- …" line, or ""
    failed: list[str]  # the "[FAIL] spec" lines (and TIMEDOUT, PANICKED, INTERRUPTED)


def verdict(run_log: str) -> Verdict:
    """From ginkgo's closing lines: "Ran N of M Specs", the pass/fail counts, and under
    "Summarizing N Failures" each failed spec (the location on the line under it is not
    kept)."""
    lines = run_log.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    ran = next((line for line in reversed(lines) if _RAN.match(line)), "")
    counts = next((line for line in reversed(lines) if _COUNTS.match(line)), "")
    failed: list[str] = []
    # sed -n '/^Summarizing/,/^Ran /p': from a start line through the next end line (or
    # the end), as often as a start line comes again.
    inside = False
    for line in lines:
        if not inside:
            if not _SUMMARY.match(line):
                continue
            inside = True
        elif line.startswith("Ran "):
            inside = False
        if _FAILED.match(line):
            failed.append(line[2:])
    return Verdict(ran, counts, failed)


def report(
    v: Verdict, arch: str, status: int, *, github: bool, summary: str | None, out=sys.stdout
) -> None:
    """The verdict on stdout, and in CI as error annotations and in the job summary."""
    print(file=out)
    print(f">> node conformance on {arch}: {v.ran or 'the suite did not finish'}", file=out)
    if v.counts:
        print(f"   {v.counts}", file=out)
    if v.failed:
        print("   failed:", file=out)
        for spec in v.failed:
            print(f"     {spec}", file=out)
    if not github:
        return
    for spec in v.failed:
        if spec:
            print(f"::error title=node conformance ({arch})::{spec}", file=out)
    if status != 0 and not v.failed:
        print(
            f"::error title=node conformance ({arch})::the suite exited {status} without a "
            "list of failures; see run.log",
            file=out,
        )
    lines = [
        f"### Node conformance ({arch})",
        "",
        v.ran or f"The suite did not finish (exit {status}).",
    ]
    if v.counts:
        lines += ["", v.counts]
    if v.failed:
        lines += [""] + [f"- {spec}" for spec in v.failed]
    with open(summary or os.devnull, "a") as f:
        f.write("\n".join(lines) + "\n")


def conclude(v: Verdict, arch: str, status: int, env=os.environ, out=sys.stdout) -> int:
    report(
        v, arch, status,
        github=bool(env.get("GITHUB_ACTIONS")), summary=env.get("GITHUB_STEP_SUMMARY"), out=out,
    )  # fmt: skip
    out.flush()
    if status != 0:
        print(
            f">> node conformance FAILED — {STATE}/run.log, {STATE}/results/, {LOG}",
            file=sys.stderr,
        )
        return 1
    print(">> node conformance OK", file=out)
    return 0


class Node:
    def __init__(self, machine: Machine):
        self.machine = machine
        self.proc: subprocess.Popen | None = None
        self.config = f"{STATE}/ssh/config"

    def ssh(self, *args: str, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["ssh", "-F", self.config, "-o", "ServerAliveInterval=30", "kaetzli", *args],
            check=False, **kwargs,
        )  # fmt: skip

    def out(self, *args: str) -> str:
        return self.ssh(*args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True).stdout

    def start(self) -> None:
        m = self.machine
        with open(LOG, "wb") as log:
            self.proc = subprocess.Popen(
                [m.qemu, *m.argv()], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT
            )

    def stop(self) -> None:
        if self.proc is None:
            return
        if os.environ.get("KEEP", "0") == "1" and self.proc.poll() is None:
            print(f">> KEEP=1: the guest is still running (pid {self.proc.pid}).")
            print(
                f"   ssh -F {STATE}/ssh/config kaetzli      "
                "# root; the suite is in /var/lib/node-e2e"
            )
            print(f"   kill {self.proc.pid}; rm -rf {STATE}      # when done")
            return
        with contextlib.suppress(OSError):
            self.proc.terminate()
        self.proc.wait()
        shutil.rmtree(STATE / "ssh", ignore_errors=True)
        for name in ("var.img", "var.img.machine-id"):
            (STATE / name).unlink(missing_ok=True)

    def wait_for_ssh(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while self.ssh("true", stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL).returncode != 0:  # fmt: skip
            if self.proc.poll() is not None:
                raise Refused(f"the guest exited during boot; see {LOG}")
            if time.monotonic() >= deadline:
                raise Refused(f"no ssh after {int(timeout)}s; see {LOG}")
            time.sleep(3)

    def versions(self) -> str:
        # cut -d' ' -f2, awk 'NR == 1 {print "CRI-O " $3}', head -1, as bash had them.
        kubelet = self.out("kubelet", "--version").rstrip("\n")
        kubelet = kubelet.split(" ")[1] if " " in kubelet else kubelet
        crio = [*self.out("crio", "--version").split("\n")[0].split(), "", "", ""][2]
        crun = self.out("crun", "--version").split("\n")[0]
        kernel = self.out("uname", "-r").rstrip("\n")
        return f"   kubelet {kubelet}, CRI-O {crio}, {crun}, kernel {kernel}"


def suite_command(focus: str, skip: str, procs: str, timeout: str) -> str:
    return (
        f"cd /var/lib/node-e2e && ./ginkgo --no-color --silence-skips --procs={procs} "
        f"--timeout={timeout} --focus={shlex.quote(focus)} --skip={shlex.quote(skip)} "
        "./e2e_node.test -- "
        "--container-runtime-endpoint=unix:///run/crio/crio.sock "
        "--container-runtime-process-name=/usr/bin/crio --container-runtime-pid-file= "
        "--kubelet-flags='--cgroup-driver=systemd --cgroups-per-qos=true --cgroup-root=/ "
        "--runtime-cgroups=/system.slice/crio.service' "
        "--report-dir=/var/lib/node-e2e/results --prepull-images=false"
    )


def main(argv: list[str]) -> int:
    repo.enter_root()
    env = os.environ
    rootfs = argv[0] if argv else env.get("ROOTFS") or "output/rootfs.erofs"
    kernel = argv[1] if len(argv) > 1 else env.get("KERNEL") or "rootfs/boot/bzImage"
    if not kvm_usable():
        print("note: no writable /dev/kvm on this host, skipping node conformance")
        return 0
    m = Machine.from_env(
        rootfs=rootfs, kernel=kernel, init=env.get("INIT") or "/usr/lib/systemd/systemd"
    )
    m.mem = env.get("MEM") or "6144"
    m.cpus = env.get("CPUS") or "4"
    focus = env.get("FOCUS") or r"\[NodeConformance\]"
    skip = env.get("SKIP") or r"\[Flaky\]|\[Slow\]|\[Serial\]"
    procs = env.get("PROCS") or m.cpus
    suite_timeout = env.get("SUITE_TIMEOUT") or "2h"
    node = Node(m)
    try:
        m.setup()
        m.hint = "(run ./tools/build-image.sh, or ./tools/fetch-image.sh for CI's)"
        m.preflight()
        subprocess.run(["./tools/build-node-e2e.sh"], env={**env, "ARCH": m.arch}, check=True)
        suite = f"output/node-e2e/{m.arch}"

        shutil.rmtree(STATE, ignore_errors=True)
        (STATE / "results").mkdir(parents=True)
        port = int(env["SSH_PORT"]) if env.get("SSH_PORT") else None
        m.ssh_setup(str(STATE / "ssh"), port=port, proxy=env.get("SSH_PROXY", ""))
        m.var_disk = str(STATE / "var.img")
        m.var_size = "30G"
        m.sysexts = ["kubernetes"]
        m.append_extra += " systemd.hostname=node-e2e"
        node.start()

        print(
            f">> booting {rootfs} with {kernel} and the kubernetes extension, {m.mem} MB, "
            f"{m.cpus} vCPUs",
            flush=True,
        )
        node.wait_for_ssh(float(env.get("TIMEOUT") or "300"))
        print(node.versions(), flush=True)

        print(">> the test node's fixtures", flush=True)
        fixtures = node.ssh("bash", "-s", input=FIXTURES.encode())
        if fixtures.returncode != 0:
            raise subprocess.CalledProcessError(fixtures.returncode, "ssh bash -s")
        subprocess.run(
            ["scp", "-q", "-F", node.config, f"{suite}/e2e_node.test", f"{suite}/ginkgo",
             "kaetzli:/var/lib/node-e2e/"],
            check=True,
        )  # fmt: skip

        print(
            f">> running the suite: focus {focus}, skip {skip}, {procs} processes "
            f"(full output in {STATE}/run.log)",
            flush=True,
        )
        status = tee(node, suite_command(focus, skip, procs, suite_timeout), STATE / "run.log")
        copy_results(node)
        v = verdict((STATE / "run.log").read_text(errors="replace"))
        return conclude(v, m.arch, status)
    except Refused as e:
        return refuse(e)
    except subprocess.CalledProcessError as e:
        return e.returncode or 1
    finally:
        node.stop()


def tee(node: Node, command: str, path: Path) -> int:
    """Run `command` in the guest, its output (stderr with it) on stdout and in `path`."""
    p = subprocess.Popen(
        ["ssh", "-F", node.config, "-o", "ServerAliveInterval=30", "kaetzli", command],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )  # fmt: skip
    with open(path, "wb") as f:
        for chunk in iter(lambda: p.stdout.read1(65536), b""):
            f.write(chunk)
            sys.stdout.buffer.write(chunk)
            sys.stdout.flush()
    return p.wait()


def copy_results(node: Node) -> None:
    ssh = subprocess.Popen(
        ["ssh", "-F", node.config, "-o", "ServerAliveInterval=30", "kaetzli",
         "journalctl -u crio --no-pager > /var/lib/node-e2e/results/crio.log; "
         "tar -C /var/lib/node-e2e -c results"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
    )  # fmt: skip
    untar = subprocess.run(["tar", "-x", "-C", str(STATE)], stdin=ssh.stdout, check=False)
    ssh.stdout.close()
    if ssh.wait() != 0 or untar.returncode != 0:
        print("warning: could not copy the suite's results out of the guest", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
