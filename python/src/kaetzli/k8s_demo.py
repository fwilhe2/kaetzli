"""tools/k8s-demo.py: a multi-node Kubernetes cluster out of the kaetzli image, as a demo:
boot NODES guests in qemu, turn them into one kubeadm control plane and NODES-1 workers,
and run something on it that has to cross nodes to work.

    ./tools/k8s-demo.py up                  # boot and form the cluster, then return
    ./tools/k8s-demo.py kubectl get pods -A # kubectl from the host, against it
    ./tools/k8s-demo.py ssh 2 [cmd...]      # a root shell on k8s-2
    ./tools/k8s-demo.py ps                  # the guests: running or not, ports, memory
    ./tools/k8s-demo.py down                # power everything off and delete its state

Kubernetes comes as the image's kubernetes system extension (docs/guides/sysext.md):
kubelet, kubeadm, kubectl, CRI-O and the CNI plugins built from source by packages/ with
EXTENSION=kubernetes, assembled into a sysext and a confext by tools/build-image.sh. The
image itself is unchanged. Both extension images are attached to every node the way the
root is (read-only disks, one file shared by all of them, verified by dm-verity from a
table on the kernel command line: Machine.sysexts), and systemd-sysext and
systemd-confext merge them at boot, CRI-O and kubelet start through the Upholds= the
extension ships, and the paths kubeadm writes are links into /var. So nothing is copied
into a guest and nothing on it is set up by hand: what this does after boot is kubeadm
and the pod network.

Every node gets a /var disk of its own (Machine.var_disk). A node needs one in any case
(its PKI and identity are there) and the extension refuses a tmpfs.

Networking is one segment with a router on it, the shape of a real network. The router
is one more guest from the same image, configured by nothing but credentials on its
command line (boot_router): it is the segment's switch, DHCP server and NAT, and the only
guest the host reaches directly; ssh to the nodes jumps through it. Each node has exactly
one NIC, on that segment, and boots the image as it is: its own DHCP asks, the router's
static lease answers with 10.0.0.1N, and kubelet picks that address because it is the
only one there is. Nothing about the network is node configuration.

The pod network is flannel, from its pinned release manifest, unmodified but for one
line: VXLAN between the nodes over the router's segment, with its nftables backend, which
is what the kernel's k8s.config fragment (packages/kernel/build.sh) is written for, as is
kube-proxy's nftables mode. A stock CNI DaemonSet is the point: it writes its config to
/etc/cni/net.d and its plugin to /opt/cni/bin, both links into /var the extension ships,
and delegates to the extension's bridge and host-local plugins.

Overrides, from the environment:
  NODES          number of guests, the first is the control plane   (default 3)
  CP_MEM         RAM of the control plane in MB                      (default 3072)
  WORKER_MEM     RAM of each worker in MB                            (default 2048)
  CPUS           vCPUs per guest                                     (default 2)
  VAR_SIZE       sparse /var disk per guest                          (default 10G)
  API_PORT       host port forwarded to the API server               (default 6443)
  ROUTER_MEM     RAM of the router in MB                             (default 384)
  TIMEOUT        seconds to wait for each node's ssh                 (default 300)
  ROOTFS, KERNEL the image to boot. By default this checkout's own build when there is
                 one (output/rootfs.erofs from tools/build-image.sh, with the kernel it was
                 assembled from in rootfs/boot), and otherwise the one tools/fetch-image.sh
                 downloaded from CI (OUT, ARCH as for tools/boot-qemu.sh)
  EXTENSIONS     where extensions/kubernetes.raw and confexts/kubernetes.raw are: by
                 default next to ROOTFS, which is where both tools/build-image.sh and
                 tools/fetch-image.sh put them

kubectl runs on the control plane, over ssh, so the host needs no Kubernetes binaries.
For a kubectl of your own, output/k8s/kubeconfig points at the API server's forward.

State lives in output/k8s/: console logs, the /var disks, the ssh key and config and the
kubeconfig. `down` removes it; `up` starts from nothing every time.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import hashlib
import itertools
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from kaetzli import repo
from kaetzli.qemu import Machine, Refused, refuse
from kaetzli.qemu.machine import kvm_usable

STATE = Path("output/k8s")
POD_CIDR = "10.244.0.0/16"  # flannel's "Network" in its manifest, which has to agree
SVC_CIDR = "10.96.0.0/12"
ROUTER = "k8s-router"
ROUTER_IP = "10.0.0.1"
ME = "./tools/k8s-demo.py"

# flannel's release manifest, pinned to the bytes it had when FLANNEL_VERSION was chosen
# and kept in downloads/k8s/. One line differs from upstream's: the nftables backend, for
# a kernel with no legacy iptables tables (packages/kernel/build.sh), checked, so that a
# manifest whose default moved is an error rather than a silent no-op.
FLANNEL_VERSION = "v0.28.10"
FLANNEL_SHA256 = "393bdbce53eb50c8eb97dd513b218518b7d6e7985dc7b889be956e112a711084"

CURL_DOWNLOAD = ["curl", "--location", "--fail", "--silent", "--show-error",
                 "--retry", "5", "--retry-all-errors", "--retry-delay", "2"]  # fmt: skip


class Failed(Exception):
    """A step failed; what it says has been printed. The exit code is args[0]."""


def node_name(n: int) -> str:
    return f"k8s-{n}"


def node_ip(n: int) -> str:
    return f"10.0.0.{10 + n}"


def node_mac(n: int) -> str:
    return f"52:54:00:6b:38:{n:02x}"  # 0 is the router's


def say(text: str) -> None:
    print(f"\n\033[1m>> {text}\033[0m", flush=True)


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


_print_lock = threading.Lock()


def node_ssh(
    n: int,
    *args: str,
    input: bytes | None = None,
    capture: bool = False,
    prefix: str = "",
    stderr=None,
) -> subprocess.CompletedProcess:
    """ssh into node N as root; the arguments are one remote command, joined by spaces as
    bash's "$*" joins them. With `prefix`, each line of its output gets it."""
    argv = ["ssh", "-o", "ConnectTimeout=10", "-F", f"{STATE}/ssh_config", node_name(n),
            " ".join(args)]  # fmt: skip
    stdin = subprocess.DEVNULL if input is None and (capture or prefix) else None
    if not prefix:
        return subprocess.run(
            argv, input=input, stdin=stdin if input is None else None,
            stdout=subprocess.PIPE if capture else None, stderr=stderr, check=False,
        )  # fmt: skip
    p = subprocess.Popen(
        argv, stdin=subprocess.PIPE if input is not None else stdin,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )  # fmt: skip
    if input is not None:
        p.stdin.write(input)
        p.stdin.close()
    for line in p.stdout:
        with _print_lock:
            sys.stdout.buffer.write(prefix.encode() + line)
            sys.stdout.flush()
    return subprocess.CompletedProcess(argv, p.wait())


def must(p: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
    if p.returncode != 0:
        raise Failed(p.returncode)
    return p


def out(n: int, *args: str) -> str:
    return must(node_ssh(n, *args, capture=True)).stdout.decode(errors="replace")


def k(
    *args: str, input: bytes | None = None, capture: bool = False, stderr=None
) -> subprocess.CompletedProcess:
    """kubectl on the control plane, with the admin kubeconfig kubeadm wrote there. Each
    argument is quoted for the remote shell; stdin goes along, for `apply -f -`."""
    quoted = "".join(f"{shlex.quote(a)} " for a in args)
    return node_ssh(
        1, f"kubectl --kubeconfig=/etc/kubernetes/admin.conf {quoted}", input=input,
        capture=capture, stderr=stderr,
    )  # fmt: skip


def retry(seconds: float, what: str, attempt) -> None:
    """Wait until attempt() is true, for up to `seconds`; `what` names it in the error."""
    until = time.monotonic() + seconds
    while not attempt():
        if time.monotonic() >= until:
            print(f"error: timed out waiting for: {what}", file=sys.stderr)
            raise Failed(1)
        time.sleep(3)


def quiet_ssh(n: int, *args: str) -> bool:
    return (
        subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=10",
                "-F",
                f"{STATE}/ssh_config",
                node_name(n),
                " ".join(args),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


@dataclasses.dataclass
class Cluster:
    base: Machine
    nodes: int = 3
    cp_mem: str = "3072"
    worker_mem: str = "2048"
    api_port: str = "6443"
    router_mem: str = "384"
    timeout: float = 300
    key_credential: str = ""
    token: str = ""
    ca_hash: str = ""
    k8s_version: str = ""

    @classmethod
    def from_env(cls, base: Machine, env=os.environ) -> Cluster:
        return cls(
            base=base,
            nodes=int(env.get("NODES") or "3"),
            cp_mem=env.get("CP_MEM") or "3072",
            worker_mem=env.get("WORKER_MEM") or "2048",
            api_port=env.get("API_PORT") or "6443",
            router_mem=env.get("ROUTER_MEM") or "384",
            timeout=float(env.get("TIMEOUT") or "300"),
        )

    # --- the guests ------------------------------------------------------------------

    def router_machine(self) -> tuple[Machine, list[str], int | None]:
        """The router: the same image again, and the only guest the host can reach
        directly. Its qemu holds the cluster segment (a hub with one unix stream socket
        per node, so it is the segment's switch) and its user-mode NIC is the way out,
        with the demo's only two port forwards (ssh to the router, the API server).
        Everything it does as a router is systemd-networkd and systemd-socket-proxyd,
        handed in as credentials:

          - 10.0.0.1 on the segment, a DHCP server whose static leases give node N the
            address node_ip(N) by its MAC, and a masquerade out through the user-mode NIC.
            DNS is qemu's resolver, which the nodes reach through that masquerade.
          - The API server forward: a socket unit on 6443 relaying to node 1.

        slirp itself cannot be on the segment (a hub floods, slirp sees the SYNs between
        nodes, tries the same connections on the host's network and resets them), which
        is why the router is a guest and not qemu's NAT (docs/guides/sysext.md, "The
        demo's network"). Returns the machine, the arguments after its argv, and the ssh
        port."""
        m = dataclasses.replace(self.base, var_disk="", mem=self.router_mem, cpus="1")
        env = os.environ
        port = m.ssh_setup(
            f"{STATE}/ssh",
            port=int(env["SSH_PORT"]) if env.get("SSH_PORT") else None,
            proxy=env.get("SSH_PROXY", ""),
            keep_key=env.get("SSH_KEEP_KEY", "0") == "1",
        )
        config = Path(f"{STATE}/ssh/config").read_text()
        (STATE / "ssh_config").write_text(
            re.sub(r"^Host kaetzli$", f"Host {ROUTER}", config, flags=re.MULTILINE)
        )
        # The authorized key, for the nodes, which get the same one.
        self.key_credential = m.append_extra
        m.nic_extra += f",hostfwd=tcp:127.0.0.1:{self.api_port}-:6443"

        lan = (
            f"[Match]\nMACAddress={node_mac(0)}\n\n[Network]\nAddress={ROUTER_IP}/24\n"
            "DHCPServer=yes\nIPMasquerade=ipv4\nLinkLocalAddressing=no\nIPv6AcceptRA=no\n\n"
            "[DHCPServer]\nPoolOffset=100\nEmitDNS=yes\nDNS=10.0.2.3\n"
        )
        for n in range(1, self.nodes + 1):
            lan += f"\n[DHCPServerStaticLease]\nMACAddress={node_mac(n)}\nAddress={node_ip(n)}\n"
        socket_unit = "[Socket]\nListenStream=6443\n"
        service = f"[Service]\nExecStart=/usr/lib/systemd/systemd-socket-proxyd {node_ip(1)}:6443\n"
        m.append_extra += (
            f" systemd.hostname={ROUTER}"
            f" systemd.set_credential_binary=network.network.10-k8s-lan:{b64(lan)}"
            f" systemd.set_credential_binary=systemd.extra-unit.k8s-api.socket:{b64(socket_unit)}"
            f" systemd.set_credential_binary=systemd.extra-unit.k8s-api.service:{b64(service)}"
            " systemd.wants=k8s-api.socket"
        )
        segment: list[str] = []
        for n in range(1, self.nodes + 1):
            segment += [
                "-netdev",
                f"stream,id=node{n},server=on,addr.type=unix,"
                f"addr.path={STATE}/{node_name(n)}.sock",
                "-netdev", f"hubport,id=port{n},hubid=0,netdev=node{n}",
            ]  # fmt: skip
        segment += [
            "-netdev", "hubport,id=lan,hubid=0",
            "-device", f"virtio-net-pci,netdev=lan,mac={node_mac(0)}",
        ]  # fmt: skip
        return m, [*segment, "-pidfile", f"{STATE}/{ROUTER}.pid"], port

    def boot_router(self) -> None:
        m, extra, port = self.router_machine()
        start(m, extra, STATE / f"{ROUTER}.console.log")
        print(
            f"   {ROUTER}  {ROUTER_IP}  {self.router_mem} MB  ssh on 127.0.0.1:{port}, "
            f"API server on 127.0.0.1:{self.api_port}",
            flush=True,
        )
        # A node's NIC connects to its socket once, at start, so the router has to be
        # listening first.
        for n in range(1, self.nodes + 1):
            sock = f"{STATE}/{node_name(n)}.sock"
            retry(10, f"test -S {sock}", lambda sock=sock: _is_socket(sock))

    def node_machine(self, n: int) -> tuple[Machine, list[str]]:
        """One node: the same machine tools/boot-qemu.sh boots, plus a /var disk, a
        hostname and the kubernetes extension, with its one NIC on the router's segment
        instead of a NAT of its own. Nothing about the network is configured on the node:
        the image's own 20-wired.network asks for DHCP, and the router's static lease
        answers."""
        name, ip = node_name(n), node_ip(n)
        config = Path(f"{STATE}/ssh/config").read_text()
        config = re.sub(r"^Host kaetzli$", f"Host {name}", config, flags=re.MULTILINE)
        config = re.sub(
            r"^    HostName 127\.0\.0\.1$", f"    HostName {ip}\n    ProxyJump {ROUTER}",
            config, flags=re.MULTILINE,
        )  # fmt: skip
        config = re.sub(r"^    Port .*\n", "", config, flags=re.MULTILINE)
        with open(STATE / "ssh_config", "a") as f:
            f.write(config)
        m = dataclasses.replace(
            self.base,
            append_extra=f"{self.key_credential} systemd.hostname={name}",
            nic_args=[
                "-netdev",
                f"stream,id=net0,server=off,addr.type=unix,addr.path={STATE}/{name}.sock",
                "-device", f"virtio-net-pci,netdev=net0,mac={node_mac(n)}",
            ],
            var_disk=f"{STATE}/{name}-var.img",
            mem=self.cp_mem if n == 1 else self.worker_mem,
            sysexts=["kubernetes"],
        )  # fmt: skip
        return m, ["-pidfile", f"{STATE}/{name}.pid"]

    def boot_node(self, n: int) -> None:
        m, extra = self.node_machine(n)
        start(m, extra, STATE / f"{node_name(n)}.console.log")
        print(f"   {node_name(n)}  {node_ip(n)}  {m.mem} MB", flush=True)

    # --- the cluster -----------------------------------------------------------------

    def init_control_plane(self) -> None:
        hexed = os.urandom(11).hex()
        self.token = f"{hexed[:6]}.{hexed[6:22]}"
        kubeadm = f"""apiVersion: kubeadm.k8s.io/v1beta4
kind: InitConfiguration
bootstrapTokens:
  - token: {self.token}
localAPIEndpoint:
  advertiseAddress: {node_ip(1)}
  bindPort: 6443
nodeRegistration:
  name: {node_name(1)}
  criSocket: unix:///run/crio/crio.sock
---
apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
kubernetesVersion: {self.k8s_version}
clusterName: kaetzli
networking:
  podSubnet: {POD_CIDR}
  serviceSubnet: {SVC_CIDR}
apiServer:
  certSANs: [127.0.0.1, localhost]
---
apiVersion: kubeproxy.config.k8s.io/v1alpha1
kind: KubeProxyConfiguration
mode: nftables
"""
        must(node_ssh(1, "cat > /run/kubeadm.yaml", input=kubeadm.encode()))
        must(node_ssh(1, "kubeadm init --config /run/kubeadm.yaml"))

        # The admin kubeconfig, pointed at the host's forward rather than the cluster
        # segment the host cannot reach.
        admin = out(1, "cat", "/etc/kubernetes/admin.conf")
        kubeconfig = STATE / "kubeconfig"
        kubeconfig.write_text(
            re.sub(r"server: https://.*", f"server: https://127.0.0.1:{self.api_port}", admin)
        )
        kubeconfig.chmod(0o600)

        digest = out(
            1,
            "openssl x509 -pubkey -noout -in /etc/kubernetes/pki/ca.crt |\n"
            "        openssl pkey -pubin -outform der | sha256sum",
        )
        self.ca_hash = f"sha256:{digest.split(' ')[0]}"

    def join_worker(self, n: int) -> None:
        join = f"""apiVersion: kubeadm.k8s.io/v1beta4
kind: JoinConfiguration
discovery:
  bootstrapToken:
    apiServerEndpoint: {node_ip(1)}:6443
    token: {self.token}
    caCertHashes: [{self.ca_hash}]
nodeRegistration:
  name: {node_name(n)}
  criSocket: unix:///run/crio/crio.sock
"""
        prefix = f"[{node_name(n)}] "
        must(node_ssh(n, "cat > /run/kubeadm.yaml", input=join.encode(), prefix=prefix))
        must(node_ssh(n, "kubeadm join --config /run/kubeadm.yaml", prefix=prefix))

    def on_workers(self, fn) -> None:
        """fn(n) for every worker, in parallel; fails if any of them did."""
        workers = range(2, self.nodes + 1)
        with ThreadPoolExecutor(max_workers=max(len(workers), 1)) as pool:
            futures = [pool.submit(fn, n) for n in workers]
        failed = [f for f in futures if f.exception() is not None]
        if failed:
            raise Failed(1)

    def pod_network(self) -> None:
        """flannel takes the /24 the controller manager gives each node, writes the CNI
        config and installs its plugin through the extension's links into /var, and
        routes between nodes over VXLAN; a node turns Ready once its config exists."""
        must(k("apply", "-f", "-", input=flannel_manifest()))
        must(k("-n", "kube-flannel", "rollout", "status", "daemonset/kube-flannel-ds",
               "--timeout=300s"))  # fmt: skip
        for n in range(1, self.nodes + 1):
            cidr = must(
                k("get", "node", node_name(n), "-o", "jsonpath={.spec.podCIDR}", capture=True)
            ).stdout.decode()
            cni = out(n, "ls", "/etc/cni/net.d").replace("\n", " ")
            print(f"   {node_name(n)}  pods in {cidr}, {cni}", flush=True)

    def demo(self) -> None:
        """Something that only works if the cluster does: four replicas of agnhost's HTTP
        server (the Kubernetes e2e tests' own utility image), spread over the workers,
        behind a Service. Then one pod asks the Service, by its DNS name, who it is
        talking to, which goes pod, CoreDNS, kube-proxy's nftables, a pod on whichever
        node, and back."""
        say("Deploying the demo: 4 × agnhost netexec behind Service 'hello'")  # noqa: RUF001
        must(k("apply", "-f", "-", input=DEMO.encode()))
        must(k("rollout", "status", "deployment/hello", "--timeout=300s"))
        must(k("get", "pods", "-l", "app=hello", "-o", "wide"))

        def get(*args: str) -> str:
            return must(k(*args, capture=True)).stdout.decode()

        pod = get("get", "pods", "-l", "app=hello", "-o", "jsonpath={.items[0].metadata.name}")
        ip = get("get", "pod", pod, "-o", "jsonpath={.status.podIP}")
        node = get("get", "pod", pod, "-o", "jsonpath={.spec.nodeName}")
        where_text = get(
            "get", "pods", "-l", "app=hello", "-o",
            'jsonpath={range .items[*]}{.metadata.name} {.spec.nodeName}{"\\n"}{end}',
        )  # fmt: skip
        where = dict(line.split(" ", 1) for line in where_text.splitlines() if " " in line)
        say(
            f"From pod {pod} on {node}: 32 requests to http://hello.default.svc.cluster.local/hostname"
        )
        # netexec's /dial makes the requests from inside the pod and reports who answered.
        dial = node_ssh(
            1,
            f"curl -fsS 'http://{ip}:8080/dial?request=hostname&protocol=http"
            "&host=hello.default.svc.cluster.local&port=80&tries=32'",
            capture=True,
        ).stdout.decode(errors="replace")
        answers = Counter(m.strip('"') for m in re.findall(r'"hello-[a-z0-9-]*"', dial))
        for name in sorted(answers):
            print(f"   {answers[name]:2d} × {name} on {where.get(name, '')}", flush=True)  # noqa: RUF001

        # The point of the demo, checked: the answers came from more than one node, so the
        # Service reached across the cluster network. With pods on two workers, all 32
        # requests landing on one node's pods by chance is (3/4)^32 at worst, 1 in 10^4.
        nodes = len({where[name] for name in answers if name in where})
        want = min(self.nodes - 1, 2)
        if nodes < want:
            print(
                f"error: the Service answered from {nodes} node(s), expected {want}",
                file=sys.stderr,
            )
            raise Failed(1)

    def up(self) -> None:
        down()
        STATE.mkdir(parents=True)
        b = self.base
        say(
            f"Booting {self.nodes} nodes from {b.rootfs} with {b.kernel}, and the kubernetes "
            f"extension from {b.extensions}"
        )
        self.boot_router()
        for n in range(1, self.nodes + 1):
            self.boot_node(n)
        for n in range(1, self.nodes + 1):
            retry(self.timeout, f"node_ssh {n} true", lambda n=n: quiet_ssh(n, "true"))

        # What the extension started by itself, through Upholds=. kubelet is up but
        # waiting for kubeadm, so CRI-O answering is the readiness that matters.
        say("CRI-O from the extension, on every node")
        for n in range(1, self.nodes + 1):
            retry(120, f"node_ssh {n} crictl info", lambda n=n: quiet_ssh(n, "crictl", "info"))
        self.k8s_version = [*out(1, "kubelet", "--version").split(), "", ""][1]
        crio = ([*out(1, "crio", "--version").split("\n")[0].split(), "", "", ""])[2]
        crun = out(1, "crun", "--version").split("\n")[0]
        print(f"   kubelet {self.k8s_version}, CRI-O {crio}, {crun}", flush=True)

        say(f"kubeadm init on {node_name(1)}")
        self.init_control_plane()

        say("kubeadm join on the workers")
        self.on_workers(self.join_worker)

        say(f"Pod network: flannel {FLANNEL_VERSION}")
        self.pod_network()
        must(k("wait", "--for=condition=Ready", "nodes", "--all", "--timeout=300s"))
        # kube-proxy too: a worker that joined seconds ago may not have its Service rules
        # yet, and the demo would then only ever reach the pods on its own node.
        must(k("-n", "kube-system", "rollout", "status", "deployment/coredns", "--timeout=300s"))
        must(k("-n", "kube-system", "rollout", "status", "daemonset/kube-proxy", "--timeout=300s"))

        say("The cluster")
        must(k("get", "nodes", "-o", "wide"))
        print(flush=True)
        must(k("get", "pods", "-A", "-o", "wide"))

        self.demo()

        second = node_name(2)
        print(
            f"""
>> The cluster is up and stays up. From here:

   {ME} kubectl get pods -A     # or: export KUBECONFIG={STATE}/kubeconfig
   {ME} ssh 1                   # root on {node_name(1)}; also: ssh -F {STATE}/ssh_config {second}
   {ME} down                    # power off and delete {STATE}""",
            flush=True,
        )


DEMO = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: hello
spec:
  replicas: 4
  selector:
    matchLabels: {app: hello}
  template:
    metadata:
      labels: {app: hello}
    spec:
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels: {app: hello}
      containers:
        - name: agnhost
          image: registry.k8s.io/e2e-test-images/agnhost:2.53
          args: [netexec, --http-port=8080]
          ports: [{containerPort: 8080}]
          readinessProbe:
            httpGet: {path: /healthz, port: 8080}
---
apiVersion: v1
kind: Service
metadata:
  name: hello
spec:
  selector: {app: hello}
  ports: [{port: 80, targetPort: 8080}]
"""


def _is_socket(path: str) -> bool:
    try:
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


def start(m: Machine, extra: list[str], console: Path) -> None:
    """qemu in the background, its console in `console`; -pidfile in `extra` says where
    its pid is, which is how the other commands find it."""
    with open(console, "wb") as log:
        subprocess.Popen(
            [m.qemu, *m.argv(), *extra],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )  # fmt: skip


def flannel_manifest() -> bytes:
    path = Path(f"downloads/k8s/kube-flannel-{FLANNEL_VERSION}.yml")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        part = Path(f"{path}.part")
        subprocess.run(
            [*CURL_DOWNLOAD, "-o", str(part),
             f"https://github.com/flannel-io/flannel/releases/download/{FLANNEL_VERSION}/kube-flannel.yml"],
            check=True,
        )  # fmt: skip
        part.rename(path)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != FLANNEL_SHA256:
        print(
            f"error: {path} is not the manifest {FLANNEL_VERSION} was pinned to — delete it "
            "and try again",
            file=sys.stderr,
        )
        path.unlink()
        raise Failed(1)
    if b'"EnableNFTables": false' not in data:
        print(f"error: {path} has no EnableNFTables line to set", file=sys.stderr)
        raise Failed(1)
    return data.replace(b'"EnableNFTables": false', b'"EnableNFTables": true')


def pids() -> list[tuple[str, int]]:
    found = []
    for pidfile in sorted(STATE.glob("*.pid")):
        try:
            found.append((pidfile.stem, int(pidfile.read_text().strip())))
        except (OSError, ValueError):
            continue
    return found


def alive(pid: int) -> bool:
    # A qemu this process started (test/k8s.py runs up and down in one) is a zombie once
    # it exits, and kill(pid, 0) still finds it until it is reaped.
    with contextlib.suppress(ChildProcessError):
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def down() -> None:
    for _, pid in pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
        # Gone before its ports and its place on the cluster segment are reused.
        while alive(pid):
            time.sleep(0.2)
    shutil.rmtree(STATE, ignore_errors=True)


def ps_nodes() -> None:
    """One line per guest this state directory knows about: whether its qemu is still
    alive, how long it has been up and how much of the host's memory it really uses (the
    RSS, not the -m it was given), where to reach it, and what the cluster thinks of it.
    The last column is asked of the API server with a short timeout, so a cluster that is
    down or still forming shows "-" rather than hanging the command."""
    guests = pids()
    if not guests:
        print(f"no guests ({ME} up)")
        return
    nodes_text = k(
        "get", "nodes", "--request-timeout=5s", "--no-headers",
        capture=True, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    nodes = {}
    if nodes_text.returncode == 0:
        for line in nodes_text.stdout.decode(errors="replace").splitlines():
            f = line.split()
            if f:
                nodes[f[0]] = f
    row = "{:<10} {:<8} {:<8} {:<11} {:<10} {:<11} {:<16} {:<14} {}"
    print(row.format("NODE", "PID", "STATE", "UP", "ADDRESS", "RSS/RAM", "SSH", "ROLE", "STATUS"))
    for name, pid in guests:
        if not name.startswith("k8s-"):
            continue
        state, up, rss = "exited", "-", "-"
        if alive(pid):
            state = "running"
            up = _ps(pid, "etime")
            rss = f"{int(_ps(pid, 'rss') or 0) // 1024}M"
        mem = _qemu_mem(pid) or "-"
        f = nodes.get(name, [])
        if name == ROUTER:
            address, role = ROUTER_IP, "router"
            ssh = f"127.0.0.1:{_router_port()}"
        else:
            address, ssh = node_ip(int(name.removeprefix("k8s-"))), f"via {ROUTER}"
            role = ("worker" if f[2] == "<none>" else f[2]) if len(f) > 2 else ""
        ready = f[1] if len(f) > 1 else ""
        print(
            row.format(
                name, pid, state, up, address, f"{rss}/{mem}M", ssh, role or "-", ready or "-"
            )
        )


def _ps(pid: int, field: str) -> str:
    p = subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True)
    return p.stdout.strip()


def _qemu_mem(pid: int) -> str:
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return ""
    for a, b in itertools.pairwise(args):
        if a == b"-m":
            return b.decode()
    return ""


def _router_port() -> str:
    on = False
    for line in (STATE / "ssh_config").read_text().splitlines():
        f = line.split()
        if f and f[0] == "Host":
            on = len(f) > 1 and f[1] == ROUTER
        elif on and f and f[0] == "Port":
            return f[1] if len(f) > 1 else ""
    return ""


def base_machine(env=os.environ) -> Machine:
    """The image every guest boots, as `up` picks it: this checkout's build by default,
    else what tools/fetch-image.sh downloaded (test/qemu-lib.sh's fetched_image_defaults)."""
    m = Machine.from_env(env, rootfs=env.get("ROOTFS", ""), kernel=env.get("KERNEL", ""),
                         init=env.get("INIT") or "/usr/lib/systemd/systemd")  # fmt: skip
    m.setup()
    m.var_size = env.get("VAR_SIZE") or "10G"
    if (
        not m.rootfs
        and not m.kernel
        and m.arch == repo.normalize_arch()
        and os.path.isfile("output/rootfs.erofs")
        and os.path.isfile("rootfs/boot/bzImage")
    ):
        m.rootfs, m.kernel = "output/rootfs.erofs", "rootfs/boot/bzImage"
    out_dir = env.get("OUT") or (
        "output/boot-image" if m.arch == repo.normalize_arch() else f"output/boot-image-{m.arch}"
    )
    m.kernel = m.kernel or f"{out_dir}/bzImage"
    m.rootfs = m.rootfs or f"{out_dir}/rootfs.erofs"
    m.hint = "(run ./tools/build-image.sh, or ./tools/fetch-image.sh for CI's)"
    m.preflight()
    m.extensions = env.get("EXTENSIONS") or os.path.dirname(m.rootfs)
    for f in ("extensions/kubernetes.raw", "confexts/kubernetes.raw"):
        if not os.path.isfile(f"{m.extensions}/{f}"):
            raise Refused(
                f"missing {m.extensions}/{f} (./tools/build-image.sh builds it with the image,\n"
                "       ./tools/fetch-image.sh downloads CI's)"
            )
    return m


def up() -> None:
    cluster = Cluster.from_env(base_machine())
    if cluster.nodes < 2:
        raise Refused(f"NODES={cluster.nodes} — a multi-node cluster needs at least 2")
    if not kvm_usable():
        print(f"warning: no KVM — {cluster.nodes} emulated guests will take a long time to do this")
    try:
        cluster.up()
    except (Failed, subprocess.CalledProcessError):
        print(
            f"\n>> failed — consoles are in {STATE}/*.console.log; {ME} down when done",
            file=sys.stderr,
        )
        raise


def main(argv: list[str]) -> int:
    repo.enter_root()
    command = argv[0] if argv else ""
    try:
        if command == "up":
            up()
        elif command == "down":
            down()
            print(">> cluster gone")
        elif command == "kubectl":
            return k(*argv[1:]).returncode
        elif command == "ps":
            ps_nodes()
        elif command == "ssh":
            if len(argv) < 2:
                print(f"{ME}: usage: {ME} ssh <node number> [command...]", file=sys.stderr)
                return 1
            n = int(argv[1])
            if len(argv) > 2:
                return node_ssh(n, *argv[2:]).returncode
            os.execvp("ssh", ["ssh", "-F", f"{STATE}/ssh_config", node_name(n)])
        else:
            print(__doc__.strip())
            return 2
    except Refused as e:
        return refuse(e)
    except Failed as e:
        return e.args[0] if e.args else 1
    except subprocess.CalledProcessError as e:
        return e.returncode or 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
