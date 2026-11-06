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
- **`kiosk`**: a fullscreen Wayland session on tty1: cage (wlroots, software rendering)
  running foot, as the `kiosk` user. Needs the `display` kernel
  (`rootfs/boot/bzImage-display`) and a screen (`QEMU_GRAPHICS=1` or `window` in
  `test/qemu-lib.sh`); on the default kernel `kiosk.service` fails, by design.
  `test/kiosk.sh` checks it.

The base provides the kernel, `crun`, `conmon`, and libraries used by the extensions.
Extensions can be combined if they do not install conflicting paths.

## Attach an extension

For QEMU, attach extensions at boot with `SYSEXTS`, a space-separated list:

```sh
SYSEXTS="podman debug" ./tools/boot-qemu.sh
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
`extensions/<name>/`. A package may compile against base packages and packages of its own
extension, named in `DEPENDS=`: `build.sh` merges their staging trees over glibc into the
build's sysroot. CI builds such an extension's chained packages in one job
(`tools/build-chain.sh`). The extension assembler removes headers, `.pc` files and other
link-time files. `./tools/build-image.sh` assembles each staged tree into a sysext and,
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
./tools/k8s-demo.sh up
./tools/k8s-demo.sh kubectl get pods -A
./tools/k8s-demo.sh down
```

The demo uses KVM, about 8 GB of RAM, and container images from `registry.k8s.io` and
`ghcr.io`. `test/sysext.sh` verifies extension merging and dm-verity behavior; `test/k8s.sh`
runs the cluster demo in CI.

## Kiosk

The `kiosk` extension boots into a fullscreen Wayland session on tty1: cage running WPE
WebKit's MiniBrowser as the `kiosk` user (uid 1100). It needs the `display` kernel and a
screen. From a local build:

```sh
ROOTFS=output/rootfs.erofs KERNEL=rootfs/boot/bzImage-display SYSEXTS=kiosk MEM=2048 \
    QEMU_GRAPHICS=window ./tools/boot-qemu.sh
```

`QEMU_GRAPHICS=1` adds the same devices without a window, as `test/kiosk.sh` uses them.
`QEMU_RESOLUTION` (default `1920x1080`) is the mode the screen offers as preferred.

The browser is a spike ([the kiosk proposal](../proposals/kiosk.md), *The spike*), not
the kiosk:

- **No toolbar.** MiniBrowser has none, so `/usr/share/kiosk/start.html` has the address
  field and a patch makes <kbd>Ctrl</kbd>+<kbd>L</kbd> return to it. An address without a
  dot or with spaces is a DuckDuckGo search.
- **Software rendering.** Skia paints on the CPU with no accelerated compositing. Mesa
  (softpipe, no LLVM) is there only because WebKit insists on an EGL display.
- **Not built:** no media (GStreamer), no bubblewrap sandbox (the web process runs
  unconfined as `kiosk`), no lockdown, and no password manager.

foot is still in the extension. `kiosk.service.d/10-browser.conf` replaces its
`ExecStart=`.
On the default kernel `kiosk.service` fails, by design: there is no `/dev/dri/card0`.

Where its state lives:

| State | Location | `/var` in RAM | `/var` on a disk |
| --- | --- | --- | --- |
| The kiosk user's home: browser profile (cookies, cache, local storage), fontconfig cache | `/var/lib/kiosk` (`StateDirectory=`) | lost at each boot | kept |
| Session runtime: Wayland socket | `/run/user/1100` | lost at each boot | lost at each boot |
| The user | userdb record in the sysext | same uid every boot | same uid every boot |
| Configuration: PAM, fonts | confext | from the image | from the image |

Nothing in the session writes `/etc`. The kiosk user can write only its home, `/tmp` and
its runtime directory. Because the uid comes from the image, files on a `/var` disk keep
the right owner across boots and image updates.

With a `/var` disk, whatever a user does in the session persists in the home directory,
including the browser's cookies and logins. That does not suit a public browser: the
profile is meant to live in RAM (see [the kiosk proposal](../proposals/kiosk.md), *Taking credentials in*).
