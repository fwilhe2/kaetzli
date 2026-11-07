# Always running the latest build

**Status: proposal, partly implemented.** Implemented: a release for every green build
of `main` ([release.md](release.md)), nightly upstream updates merged when green,
verified fetching, and replacing a VM's root while its `/var` stays (`tools/vm.sh
update`/`restart`, the launcher's `update`). Not yet: everything under *Gaps*, in
particular a Kubernetes node upgrade that works without a person running it, and a way
back when a new build fails to boot.

## The idea

Most distributions get fixes to users by backporting them to a stable release. kaetzli
does it by releasing everything that passes the tests. Every night the update workflow
bumps whichever packages upstream has released, auto-merge merges the ones whose CI is
green, and each green `main` build is a snapshot. A fix released upstream in the
afternoon can be in a snapshot the same night, and in nobody's backlog.

That only helps if moving to the newest snapshot costs about as much as a reboot. Three
properties of the image are what make it that cheap:

- **The root is one immutable file.** An update replaces `rootfs.erofs`, its kernel and
  its cmdline as a set, verified by dm-verity. Nothing is installed into a running system
  and nothing drifts, so no state combines old and new packages.
- **State is only in `/var`**, on its own disk with its own machine ID. It is not part of
  the image and an update never touches it: container storage, volumes, home directories,
  `/var/lib/kubelet`.
- **Extensions come with the base.** podman, kubernetes and debug are built in the same
  run and released with it, so a matching set is always available.

## A desktop VM (the Podman-machine case)

What works today:

```sh
./tools/vm.sh update            # fetch and verify the newest snapshot
./tools/vm.sh restart dev       # stop, re-pin the image, boot it with the same /var
```

The launcher does the same with `kaetzli update` and a restart, and it warns when a newer
snapshot exists. The cost is one reboot: the containers stop and start again from the same
storage. A workload survives the reboot if it is a unit. That means a rootless Quadlet
file under `~/.config/containers/systemd/`, which lives on `/var` (`/home` →
`/var/home`), or a container with a restart policy that a unit starts. Images the
workload uses can follow the same model through `podman auto-update` (`AutoUpdate=registry`
in the Quadlet file).

What it takes for that to be "exceptionally easy" is listed under *Gaps*: nothing
updates without a command, nothing goes back by itself, and a rootful Quadlet has no
place that survives a reboot.

## A Kubernetes cluster

Nodes are cattle, and the image fits that model better than an in-place upgrade does.
The node is never upgraded. It is replaced:

1. Boot a new node from the new snapshot, with the `kubernetes` extension and a new,
   empty `/var` disk.
2. `kubeadm join` it to the cluster.
3. `kubectl cordon` and `kubectl drain` an old node. Its pods are rescheduled under their
   PodDisruptionBudgets, some of them onto the new node.
4. `kubectl delete node`, then power off the old VM and delete its disk.
5. Repeat until no node runs the old snapshot.

This is what Cluster API's MachineDeployment rollouts and Karpenter's drift replacement
do for cloud images. Done that way, kaetzli is just the image those tools roll out.
Workers can be replaced like this today, by hand: `tools/k8s-demo.py` shows that the
image forms a cluster, and `test/k8s.py` and `test/node-conformance.py` gate every build
on it. What is missing is the automation, plus three Kubernetes-specific gaps below.
Version skew is the largest of them.

## Gaps

### For every machine

1. **No unattended update.** `update` and `restart` are commands a person runs.
   Needed: a host-side timer, or a `--watch` mode in the launcher, that fetches, verifies
   and restarts in a maintenance window. A guest cannot update itself, since the host
   owns the image files.
2. **No way back.** When a new snapshot does not boot, or boots without the workload,
   the machine stays broken until someone notices. `vm.sh` deletes the previous pinned
   image as soon as it copies the new one. Needed: keep the previous image, boot the new
   one, wait for ssh and `systemctl is-system-running`, and boot the old one again when
   that fails. systemd's boot assessment (boot counting, `systemd-bless-boot`) does this
   for systemd-boot and UKIs; with direct kernel boot it has to be the host's job.
3. **`/var` only moves forward.** Software on the image may migrate its state in `/var`
   on first start (podman's database, etcd, the kubelet's checkpoints), and an older
   image may not read it afterwards. A rollback is only safe with a snapshot of `/var`
   taken before the update. `vm.sh snapshot` exists but nothing calls it automatically.
4. **No place for persistent system configuration.** `/etc` is the image's, and a
   confext is the intended route ([sysext.md](../guides/sysext.md)), but there is no
   documented workflow for a user's own: a rootful Quadlet file, a network override, a
   registry mirror. The tests use `/run`, which does not survive a reboot. Credentials
   (`systemd.extra-unit.*`, as `k8s-demo.py`'s router uses them) are one way. Needed: a
   documented user confext in `/var/lib/confexts`, rebuilt independently of the image.
5. **Snapshots expire after 37 days.** That is enough for rollback and bisecting, but it
   also rules out staying behind. That is deliberate (*The model is rolling* in
   [release.md](release.md)), and users should be told.
6. **No delayed channel.** Every machine that updates takes the newest snapshot. See
   *Risk* for why some users want a lag. Snapshots are kept long enough that `update
   --min-age 3d` (the newest snapshot that is at least three days old) would be a
   flag, not a channel.
7. **Authenticity is checked only where `gh` exists.** `fetch-image.sh` checks
   `SHA256SUMS` always, and the attestations only when `gh` can. An unattended updater
   has to check the attestation itself.

### For Kubernetes

8. **Version skew.** `packages/kubernetes` follows upstream's newest release, minor
   versions included. Kubernetes does not support a kubelet newer than its API server,
   and upgrades the control plane one minor version at a time, so a snapshot that moves
   from 1.37 to 1.38 produces worker images that a 1.37 cluster must not take. Needed,
   one of:
   - the control plane goes first: replace control-plane nodes (which needs three of
     them and a stacked etcd that tolerates a member at a time), then `kubeadm upgrade
     apply` for the cluster's configuration, then the workers
   - or a kubernetes extension per minor version (`kubernetes-1.37`, `kubernetes-1.38`
     in the same snapshot), so the base updates nightly while the cluster chooses when to
     move to the next minor. This keeps "latest base" separate from "latest Kubernetes",
     which are different decisions
9. **No control-plane replacement.** `k8s-demo.py` has one control-plane node. Replacing
   it means a new cluster. An HA control plane (three nodes, `kubeadm join
   --control-plane`) is needed before the workflow covers the whole cluster.
10. **No orchestrator integration.** Nothing turns "a new snapshot exists" into "roll the
    nodes". Candidates, in increasing size: a script on top of `k8s-demo.py` (`roll`),
    a Cluster API infrastructure provider for qemu or Firecracker, or the images as
    Karpenter or Cluster API inputs on a cloud. The first is enough to prove it, and to
    test it in CI: replace every node of a running cluster with the same image while a
    Deployment keeps answering.
11. **The node's identity is its disk.** A new node gets a new `/var` and a new machine
    ID. That is right for replacement, but it means local PersistentVolumes (hostPath,
    local-path) do not move with it. Workloads that need their data must use
    network storage, or be pinned and migrated deliberately.

## Risk: newest is not always safest

Taking upstream's newest release within a day closes known holes quickly. It also takes
in new bugs and new supply-chain compromises quickly. The xz backdoor reached Debian sid
and Fedora Rawhide through ordinary new releases, and stable distributions were spared
because they lag. The checks here (boot, systemd, network, podman, rootless, k8s, node
conformance, reproducibility, symbol and hardening checks) catch a broken build, not a
malicious one. An update that is fast by default is right for many users. Others need the
lag to be their choice (gap 6), and the Python dependencies already take it: Dependabot
proposes them only two weeks after a release.

## Who does something similar

| system | what it shares | how it differs |
| --- | --- | --- |
| Fedora CoreOS (Zincati, rpm-ostree) | automatic updates of an image-based OS, rollback, fleet-wide reboot coordination | releases on a weeks-long cadence through testing and stable streams; atomic updates in place, not replacement |
| Flatcar (update_engine, Nebraska, FLUO) | A/B image updates, a Kubernetes operator that drains and reboots | release channels with stabilization; updates in place |
| Bottlerocket (brupop, TUF) | immutable, verity root, an update operator for Kubernetes | releases every few weeks; signed TUF repository |
| Talos Linux | immutable, API-only, system extensions | versioned releases with patch backports |
| Karpenter drift, Cluster API rollouts | replace nodes when the image changes | orchestration only; bring your own image |
| Wolfi and Chainguard Images | rebuild on upstream releases within hours, SBOMs | containers, not a VM or node OS |
| NixOS unstable, Fedora Rawhide, Arch | follow upstream continuously, gated by tests | mutable package-managed systems |

Each part exists somewhere else. The combination is less common: a VM and node OS
built from source, reproducibly, released per green commit with upstream's releases
taken nightly, every build gated by boot, Kubernetes and node-conformance tests, shipped
with an SBOM and VEX, and updated by replacement. It is a fair differentiator. Calling
it a USP needs two things first: gap 2 (a way back) and gap 6 (a lag a user can choose),
since those are what the established systems give their users and what an "always
latest" claim will be asked about first.

## Suggested order

1. Keep the previous image and fall back to it automatically (gap 2), with a `/var`
   snapshot before each switch (gap 3), in `vm.sh` and the launcher.
2. `update --min-age` (gap 6) and an unattended mode of the launcher (gap 1).
3. A user confext for persistent configuration (gap 4).
4. A kubernetes extension per supported minor version (gap 8), then `k8s-demo.py roll`
   with three control-plane nodes (gaps 9, 10), tested in CI against a live Deployment.
