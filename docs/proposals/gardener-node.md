# Kaetzli as a Gardener worker node on OpenStack

**Status: proposal, partly implemented.** Done: in the base, the kernel symbols below, a
writable `/etc` and `/opt/bin`, and `kaetzli-user-data.service` ([user data](../guides/user-data.md));
the [bootable disk](../guides/disk.md) (`tools/build-disk.sh --cloud openstack`); containerd
in the `kubernetes` extension, replacing CRI-O (`test/containerd.py`); Gardener's node
init and node-agent bootstrap, and a node joining a kubeadm API server, tested in CI
([below](#what-is-verified-and-how)). Not done: getting the extension onto a node, and
gardener-extension-os-kaetzli (its own repository). Gardener facts are from
`gardener/gardener` and `gardener-extension-networking-calico` on `master`, November 2026.

A kaetzli VM has joined a shoot before (commit 1432f7a: Gardener v1.154.1,
networking-calico with its nftables dataplane, kube-proxy in nftables mode). This is about
doing it on Gardener's defaults, provisioned the way Gardener provisions any node.

The goal: a Gardener shoot on OpenStack whose worker pool boots the regular kaetzli release,
with no Gardener-only image. What a node runs goes in the `kubernetes` system extension;
the base gets only what any persistent VM node benefits from.

## What a Gardener node needs from the OS

Gardener provisions a worker through its OperatingSystemConfig (OSC): the OS extension turns
it into user data, a script that writes units and files and starts `gardener-node-init`.
That script (`nodeinit/templates/scripts/init.tpl.sh`) uses bash, curl, sed, awk and
`ctr`: it pulls the node-agent image with `ctr images pull`, mounts it, copies the binary
to `/opt/bin` and runs `gardener-node-agent bootstrap`. From then on node-agent owns the
node:

| Path | Written by node-agent |
| --- | --- |
| `/etc/systemd/system/*.service` and `.d/` | every OSC unit and drop-in, kubelet's included |
| `/etc/containerd/config.toml`, `certs.d/`, `conf.d/` | containerd's configuration |
| `/opt/bin` | `gardener-node-agent`, and kubelet |
| `/opt/cni/bin` | the CNI's plugins (its DaemonSet) |
| `/var/bin/containerruntimes` | extra runtimes, prepended to containerd's `PATH` |
| `/var/lib/gardener-node-agent`, `/var/lib/kubelet` | state |
| any path an OSC `files` entry names | sysctls, CA bundles, kubelet config |

So a node needs a writable `/etc` and `/opt/bin`, and nothing under `/usr`.

## Done in the base

**Kernel** (`packages/kernel/build.sh`, k8s fragment; `test/kernel-caps.txt`). A shoot
defaults to kube-proxy in **iptables** mode (`defaults_shoot.go`: `ProxyModeIPTables`) and
Calico with **IPIP** encapsulation. The existing fragment was written for nftables
kube-proxy and flannel, so these are new, all through `NFT_COMPAT`:

- kube-proxy (iptables): `-m statistic`, without which a service with several endpoints
  sends everything to one, plus `-m recent` (session affinity) and `-j REJECT`.
- Calico's Felix (iptables dataplane, `felix/iptables/match_builder.go`): `NET_IPIP`, `-m
  set`, `multiport`, `connmark`, `limit`, `rpfilter`.
- Cilium, Gardener's other CNI: `NET_CLS_BPF`, `NET_ACT_BPF`, `GENEVE`.

`IP_VS` stays off: IPVS is opt-in for a shoot (`kubeProxy.mode: IPVS`), and nftables is
its successor.

**Writable `/etc`**, as an overlay: the image's `/etc` is the lower layer, and writes land
in `/var/lib/extensions.mutable/etc`. This is systemd-confext's mutable mode
(`image/files/etc/systemd/confext.conf`, `Mutable=auto`), not a mount of our own, for
three reasons:
- it already composes with confexts (their layers stack under the writable upper);
- it keeps PID 1's `/etc/machine-id` bind mount;
- it merges even with no confext installed ("proceeding in mutable mode").

A drop-in makes `systemd-confext.service` run at every boot and creates the directory.
A file nobody wrote follows each new snapshot; a written one shadows it from then on.
On a VM without a `/var` disk the writes are in RAM. Under Secure Boot `/etc` stays
read-only: `/var/lib/extensions.mutable` gets the same empty read-only mount as
`/var/lib/extensions`, since an `/etc` from an unverified disk could start anything.

What reads `/etc` before `systemd-confext.service` (`sysinit.target`) sees only the
image's copy: PID 1's own configuration, journald, udevd, sysctl, modules-load. A change
to those needs a restart of the service, or a reboot.

The first boot still runs at assembly. `ldconfig.service` and `systemd-sysusers.service`
are masked. A merged sysext makes their `ConditionNeedsUpdate=/etc` true, because the
merged `/usr` is newer than `/etc/.updated`, and they would write `ld.so.cache` and the
account files into the upper layer, where they would shadow every later image's.

**Writable `/opt/bin`**: a symlink to `var/opt/bin`, as `/home` is to `var/home`. Not
all of `/opt`: systemd-sysext merges `/opt` read-only as soon as an extension ships
anything there (the kubernetes extension's `/opt/cni`), and a link inside it survives
that merge. `/opt/cni/bin` is already such a link, in the kubernetes extension.

**User data** (`packages/user-data`): `kaetzli.user-data=openstack` on the command line
makes `kaetzli-user-data.service` fetch `http://169.254.169.254/openstack/latest/user_data`
and run it, once per instance UUID. bash and curl: there is no cloud-init (constraint 5).
It is in the base because it has to run before an extension could be merged, and any
OpenStack VM benefits. No config drive yet: the kernel has no `ISO9660_FS`.

## Not done yet

**containerd and `ctr`** (`packages/containerd`, in the `kubernetes` extension, done).
Gardener's CRI is containerd, with no CRI-O path, so containerd replaced CRI-O there: one
CRI for kubeadm and Gardener alike. containerd 2.4 builds offline from its own `vendor/`. The
extension ships `containerd`, `ctr`, the runc v2 shim and `containerd.service`, upheld from
`basic.target` and started by a `kaetzli-user-data.service` drop-in before the user data
runs, since gardener-node-init runs `ctr` from it. Its confext has `/etc/containerd/config.toml`, which is
upstream's defaults with four changes:
- the shim's `BinaryName = '/usr/bin/crun'`, so runc never ships;
- `SystemdCgroup = true`;
- the CNI plugins in `/usr/libexec/cni` ahead of `/opt/cni/bin`;
- the `opt` plugin's directory in `/var`.

Node-agent keeps that file (`ensureContainerdDefaultConfig` writes `containerd config
default` only when none exists) and patches only the sandbox image, the CNI directories,
`SystemdCgroup` and the OSC's `criConfig.containerd.plugins`. `BinaryName` is CRI
configuration, so a plain `ctr run` still asks for runc. Gardener uses `ctr` only to pull
and mount the node-agent image.

**The OpenStack disk** (`tools/build-disk.sh --cloud openstack`, done): the general
[bootable disk](../guides/disk.md) with `kaetzli.user-data=openstack` on its command line.
Its machine ID is the instance UUID, from SMBIOS. Upload with `hw_firmware_type=uefi`,
`hw_disk_bus=virtio`, `hw_machine_type=q35`, on a flavor whose disk is at least the image's
virtual size (`/var` is 20 GiB by default).

**The `kubernetes` extension** has to be merged before node-agent starts. Its kubelet unit
is upheld from `multi-user.target`; node-agent writes its own `kubelet.service` into
`/etc/systemd/system`, which overrides the extension's, so whether that upholding gets in
node-agent's way is to be checked. Attached as a second verified disk it keeps dm-verity;
on the `/var` partition it would not.

## What is verified, and how

What each requirement is held to. *Config* is a check of the kernel config or a file; *one
VM* boots the image; *cluster* boots several, or a node against an API server. A blank
cell is not tested that way.

| Requirement | Config | One VM | Cluster |
| --- | --- | --- | --- |
| kube-proxy, iptables mode (`statistic`, `recent`, `REJECT`) | `kernel-caps.py` | `kernel-probes.py` | `k8s.py`, `DATAPATH=calico-iptables` |
| Calico's IPIP and iptables matches (`set`, `multiport`, `connmark`, `rpfilter`) | `kernel-caps.py` | `kernel-probes.py` | `k8s.py`, `DATAPATH=calico-iptables` |
| ipset set types Felix creates | `kernel-caps.py` | (no ipset in the image) | `k8s.py`, `DATAPATH=calico-iptables` |
| `-m limit`, `-j LOG` (nft's own `limit`, `log`) | `kernel-caps.py` | `kernel-probes.py` | |
| Cilium: tc BPF, Geneve | `kernel-caps.py` | `kernel-probes.py` (attach and create only) | |
| Gardener's VPN: `NETMAP`, `owner` | `kernel-caps.py` | `kernel-probes.py` | |
| Writable `/etc`, nothing written at boot | | `systemd.py`, `persistent-var.py` | |
| `/etc` across an update: unwritten files follow, written ones stay | | `etc-upgrade.py` | |
| Units enabled into `/etc` start on the next boot | `test_confext_dropin.py` | `gardener.py` (second boot) | |
| Writable `/opt/bin` on `/var` | | `persistent-var.py`, `secureboot.py`, `gardener.py` | |
| User data from the metadata service, once per instance | `test_user_data.py` | `user-data.py`, `disk.py` (OpenStack preset), `gardener.py` | |
| containerd up before the user data, `ctr` works | | `containerd.py` | |
| Gardener's `init.sh` and node-agent bootstrap | `check-gardener.py verify` | `gardener.py` | |
| node-agent's certificate, OSC applied, kubelet registers the Node | | | `gardener-join.py` |
| node-agent's `kubelet.service` replaces the extension's | | | `gardener-join.py` |
| The Gardener pin is current | `check-gardener.py` (nightly) | | |

Not verified anywhere: a real shoot (Gardener's own control plane, its full OSC, its
kubelet from an image), OpenStack itself (the disk boots under OVMF in qemu, not on
Nova), the Cilium and VPN datapaths beyond creating their rules and links, and the
extension delivered to a node rather than attached by the test.

## Tested in CI

`test/gardener.py` (ci.yml's `gardener` job, both arches, gating nothing) provisions a VM
as Gardener does, with Gardener's own code from a pinned release. The user data has the
shape of an OS extension's (provider-local's actuator: files, units, `daemon-reload`,
`enable` and `restart --no-block`, run once). It holds the OSC that `nodeinit.Config`
makes, with Gardener's `init.sh`, rendered byte for byte. That script pulls node-agent
from Gardener's registry with `ctr`, and node-agent's `bootstrap` writes, enables and starts
`gardener-node-agent.service` and disables `gardener-node-init`. The test checks all of
that, and that nothing hit a read-only filesystem. It ends where node-agent asks the API
server for its kubeconfig (a CSR against a host that does not exist).

`test/gardener-join.py` (the same job) takes that node further, against a kubeadm control
plane standing in for the shoot's API server. The test does by hand what Gardener's
control plane would: it approves node-agent's certificate request, binds the node-agents
group to cluster-admin (Gardener has its own authorizer), and creates the OSC Secret.
That OSC has a file in `/etc` and a kubelet unit in Gardener's shape. node-agent gets its
certificate and applies the OSC; its `kubelet.service` in `/etc/systemd/system` replaces
the extension's (which has no drop-ins to carry over). The kubelet registers the Node
through its bootstrap kubeconfig, and node-agent annotates the Node with the OSC's
checksum. Gardener's real OSC is not used: its kubelet comes from an image, and its
configuration is the extension's business.

`/var` is a disk, and the VM is then rebooted from it: the user data is neither fetched
nor run again, gardener-node-init does not start, and node-agent starts from its unit in
`/etc`'s upper layer and its binary in `/var/opt/bin`. That took a fix: the boot's
transaction is made before systemd-confext merges `/etc`, so a unit enabled there was
loaded but never started; the confext drop-in now starts the boot's targets again after
the merge.

The release is pinned in `test/gardener.env` (version, node-agent's index digest) with
`test/gardener-init.sh`, Gardener's `init.tpl.sh` rendered by Go's `text/template`.
`tools/check-gardener.py verify` holds both to upstream in the gardener job, and the
nightly update workflow proposes a new release (`check-gardener.py bump`), for a person
to merge.

## Open questions

- Does node-agent write anywhere else at runtime? Bootstrap does not, and with a small
  OSC it writes the OSC's paths, its own `/var/lib/gardener-node-agent` and kubelet's
  `/var/lib/kubelet` (`test/gardener-join.py`), all writable. A shoot's full OSC (CA
  bundles, sysctls, journald, containerd's `certs.d`) is still to be seen.
- `systemctl start` from user data waits for its unit, and a unit ordered after
  `multi-user.target` deadlocks the boot without `--no-block`. Gardener's generic shape
  (provider-local) uses `restart --no-block`; gardener-extension-os-kaetzli should too.
- Should `ISO9660_FS` be enabled for config drives? OpenStack defaults to iso9660 when one
  is requested, and some clouds give no metadata service.
