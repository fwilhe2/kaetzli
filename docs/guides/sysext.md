# System extensions

System extensions add software to the immutable base at boot. The project uses systemd
system extensions (sysexts) for `/usr` and configuration extensions (confexts) for `/etc`.
systemd merges their read-only filesystems over the base; removing an extension restores
the base view.

The base declares `SYSEXT_LEVEL=1` and `CONFEXT_LEVEL=1` in `os-release`. Extension release
metadata must match the corresponding level and `ID=kaetzli`. These levels represent the
interfaces extensions may rely on; bump them when an incompatible base change requires
rebuilding extensions.

## Available extensions

- **`podman`**: Podman, Quadlet, netavark, aardvark-dns, pasta, and rootless ID mapping
  tools.
- **`kubernetes`**: kubelet, kubeadm, kubectl, CRI-O, crictl, and CNI plugins. Its
  configuration extension supplies the matching `/etc` entries.
- **`debug`**: strace, perf, bpftool, tcpdump, gdbserver, socat, and jq.

The base provides the kernel, `crun`, `conmon`, and libraries used by the extensions.
Extensions can be combined if they do not install conflicting paths.

## Attach an extension

For QEMU, attach extensions at boot with `SYSEXTS`, a space-separated list:

```sh
SYSEXTS="podman debug" ./tools/boot-qemu.py
./tools/ssh.sh --root
```

For a persistent QEMU machine, use `--ext` with `tools/vm.sh`:

```sh
./tools/vm.sh start dev --ext podman,debug
```

The VM tools attach each extension as a read-only disk. Its dm-verity table is passed to
the kernel alongside the root table, so the kernel verifies the extension before systemd
merges it. This attachment method is also used by the Kubernetes demo.

An extension may instead be placed on a persistent `/var` disk under
`/var/lib/extensions/` or `/var/lib/confexts/`. systemd can merge it from there, but this
loading path does not verify the image with dm-verity. The base image and attached
extensions remain verified.

`systemd-sysext status` and `systemd-confext status` show the active merges in the guest.

## Build extensions

Packages declare `EXTENSION=<name>` in `env.sh` and install into
`extensions/<name>/`. `./tools/build-image.sh` assembles each staged tree into a sysext and,
when it contains `/etc` files, a confext. Outputs are written under `output/extensions/`
and `output/confexts/`; each `.raw` image has a corresponding `.dm-verity` table.

To build an extension from an existing directory tree:

```sh
./tools/build-extension.sh sysext <name> <directory>
./tools/build-extension.sh confext <name> <directory>
```

A sysext tree may contain `/usr` and `/opt`; a confext tree contains `/etc`. For a staged
package tree, the image build uses `./tools/build-extension.sh staged <name>` internally.

## Kubernetes node and demo

Kubernetes runs on the base plus the `kubernetes` sysext and confext. Paths that Kubernetes
expects to write under `/etc` are links into `/var` or `/run`; the root remains read-only.
A node needs a persistent `/var` disk for its identity and state.

Start the included three-node demo with:

```sh
./tools/k8s-demo.py up
./tools/k8s-demo.py kubectl get pods -A
./tools/k8s-demo.py down
```

The demo uses KVM, about 8 GB of RAM, and container images from `registry.k8s.io` and
`ghcr.io`. `test/sysext.py` verifies extension merging and dm-verity behavior; `test/k8s.py`
runs the cluster demo in CI.
