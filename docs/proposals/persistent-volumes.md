# Persistent volumes for Kubernetes nodes

**Status: proposal.** A cluster from the kubernetes extension, the demo
(`tools/k8s-demo.py`) included, has no StorageClass and no PersistentVolumes. A workload
that asks for storage with a PersistentVolumeClaim stays `Pending` ("no persistent volumes
available for this claim and no storage class is set"), and so does every pod that
mounts it.

## Where this came up

Running [shelf](https://github.com/fwilhe2/shelf), a home NAS, on the demo cluster with
its Kubernetes manifests (`deploy/kubernetes`): two ReadWriteOnce PVCs, one for its config
and search index, one for the shared files. Nothing on the kaetzli side was wrong, but
nothing on it provides storage either, and the usual quick fix does not work as shipped:

- **Rancher's local-path-provisioner** (the default in k3s) creates its volumes under
  `/opt/local-path-provisioner`. `/opt` is part of the read-only EROFS root, so its helper
  pod fails. With the path changed to `/var/lib/local-path-provisioner`, it works: the
  volumes land on the node's `/var` disk and survive a reboot of the node.
- **Capacity is not enforced:** local-path ignores the requested size; a 500Gi claim was
  bound on a 10 GB `/var`. A full `/var` then also takes down the node itself (kubelet,
  containerd's images, the node's PKI all live there).
- **fsGroup does not apply** to its hostPath-backed volumes; it works only because the
  provisioner creates the directories world-writable (0777).

## Constraints

- `/usr`, `/opt` and `/etc` are read-only (EROFS, sysext, confext). Anything written must
  go under `/var`, or onto a disk of its own.
- CSI drivers are pods (the FlexVolume directory is intentionally empty,
  `packages/kubernetes/build.sh`), so a node needs only what the kernel and kubelet
  provide; no host binaries.
- `/var` is the node's own state. Workload data on the same filesystem competes with it
  for space.

## Options

1. **Document local-path with a `/var` path.** No code: a pinned manifest and the one
   changed line, as the demo already does for flannel. Simple, survives reboots, but
   no capacity limits, and volumes are tied to one node.
2. **A second data disk per node**, mounted by the image (e.g. a `kaetzli-data` partition
   label, formatted on first boot like `/var`), with local-path or static `local` PVs on
   it. Workload data cannot fill up `/var`, and the disk's size is the limit.
   `vm.sh`/the launcher and `k8s-demo.py` would attach one (`DATA_SIZE`).
3. **Static `local` PersistentVolumes** on that disk, declared by the operator: no
   provisioner at all, `Retain` by default, the real size in the PV. The best fit for a
   single storage node such as a NAS.
4. **Network or replicated storage** (NFS CSI driver, Longhorn): volumes that follow a pod
   to another node. Longhorn needs iSCSI (`open-iscsi` on the host and kernel support)
   and more RAM than the demo's nodes have; the NFS CSI driver needs only the kernel's NFS
   client and a server somewhere.

## Proposed

- Option 1 in `tools/k8s-demo.py`, as an opt-in (`STORAGE=local-path`), so the demo can
  run stateful workloads, and `test/k8s.py` can check that a PVC binds, is written, and
  survives a node reboot.
- Option 2 as the real fix: a data disk, kept out of `/var`, used by option 1 or 3.
- Check the kernel config for option 4 (NFS client, iSCSI), so a CSI driver can at least
  be used; ship no host tools for it.

## Also found

- **Node reboots end the demo node.** qemu runs with `-no-reboot`, and `k8s-demo.py` has
  no command to start a stopped node again (`Cluster.boot_node` does it, with the node's
  `/var` intact). A `k8s-demo.py start N` would make reboot tests possible.
- **Local images.** An image built on the host cannot be tried on the demo without a
  registry with a trusted certificate. `/etc` is writable now, so a demo option could put a
  local registry in `/etc/containerd/certs.d` on each node.
