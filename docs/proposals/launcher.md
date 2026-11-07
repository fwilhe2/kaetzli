# A launcher without a checkout

**Status: partly implemented.** Step 2 of *Order* below: the launcher for Linux
(`go/cmd/kaetzli`: `start`, `ssh`, `console`, `stop`, `list`, `info`, `update`, `rollback`,
`rm`; updates roll back, `go/internal/rollout`),
`print-argv` held to the harness by `python/tests/test_launcher.py`, and its binaries in
every snapshot. Not yet: `run`, `restart`, `snapshot`, `restore`, `resize`; macOS and
Windows; `/var` as qcow2 (raw, as `vm.sh`'s, until Windows needs sparse files); `vm.sh` as
a wrapper around it. Before it, running kaetzli meant cloning the repository and using
`tools/vm.sh`, which needs bash and `socat`, and does not run on Windows.

## Goal

One file to download, on Linux, macOS and Windows, and one thing to install: qemu.

```sh
# Linux, macOS
curl -fLo ~/.local/bin/kaetzli \
  https://github.com/fwilhe2/kaetzli/releases/latest/download/kaetzli-$(uname -s | tr A-Z a-z)-$(uname -m)
chmod +x ~/.local/bin/kaetzli
kaetzli start --ext podman
kaetzli ssh
```

```powershell
# Windows
Invoke-WebRequest -OutFile kaetzli.exe `
  https://github.com/fwilhe2/kaetzli/releases/latest/download/kaetzli-windows-x86_64.exe
.\kaetzli.exe start --ext podman
```

The commands are `vm.sh`'s (`start`, `ssh`, `console`, `stop`, `run`, `list`, `info`,
`update`, `restart`, `snapshot`, `restore`, `rm`, `resize`), with the same options and
defaults, so the README's *Machines* section applies to both.

## Why Go

Windows has neither bash nor Python by default, so this is the case CLAUDE.md keeps Go
for: a static binary is the point. `CGO_ENABLED=0`, standard library only if possible,
cross-compiled for `linux`, `darwin` and `windows` on `amd64` and `arm64`.

## Host requirements

| | Linux | macOS | Windows |
| --- | --- | --- | --- |
| qemu | distribution package | `brew install qemu` | `winget install SoftwareFreedomConservancy.QEMU` |
| Acceleration | KVM (`/dev/kvm`) | HVF | WHPX (*Windows Hypervisor Platform* feature) |
| Image | amd64 or arm64, as the host | arm64 on Apple silicon, amd64 on Intel | amd64 |
| `ssh`, `ssh-keygen` | OpenSSH | built in | built in since Windows 10 1809 |

Nothing else: no git, no `gh`, no `socat`, no `curl` after the first download.

## What it does differently from vm.sh

- **Images come from releases** (`release.md`): it resolves the latest snapshot once,
  downloads into its cache and verifies every file against `SHA256SUMS` before using it.
  `--image DIR` boots a local build, so `vm.sh`'s `--image local` keeps a way in.
- **State in the platform's place**: `os.UserCacheDir()` for images, one directory per
  machine under `os.UserConfigDir()`. That is `~/.local/share` and `~/.cache` on Linux,
  `~/Library` on macOS and `%LOCALAPPDATA%` on Windows.
- **Console and QMP on localhost TCP** rather than Unix sockets, and the console client is
  built in, so `socat` is not needed.
- **Detached without `-daemonize`**, which qemu does not have on Windows: the launcher
  starts qemu in a new session or process group and records its pid.
- **`/var` as qcow2**, made with `qemu-img`, which comes with qemu on every platform. A
  raw file is only sparse on NTFS if it is marked so, and qcow2 makes `snapshot` and
  `resize` qemu-img operations rather than copies.
- **Keys** are made with `ssh-keygen`. `ssh` is the system's, run with the machine's
  `ssh_config`, so `scp`, `sftp` and `rsync -e` work as they do with `vm.sh`.

## One qemu command line, three implementations

The boot harness is already bash (`test/qemu-lib.sh`) and Python (`kaetzli.qemu`), held
equal by `python/tests/test_qemu_machine.py`. The launcher would be a third copy of the
`pc` profile's command line. To keep them in step, it gets a `--print-argv` option, and
that test compares its output with the other two for the same machine. A boot test in CI
runs the release binary against the image it is released with.

`vm.sh` then becomes a thin wrapper around the launcher (or is deleted), rather than a
fourth copy. That replaces the Python port of `vm.sh` planned in `host-scripting.md`,
item 7.

## Releases

The binaries are assets of every snapshot, named as `uname -s` and `uname -m` print
them so the install line works unchanged: `kaetzli-linux-x86_64`, `kaetzli-linux-aarch64`,
`kaetzli-darwin-arm64`, `kaetzli-darwin-x86_64` and `kaetzli-windows-x86_64.exe`. They are
listed in `SHA256SUMS`, built reproducibly (`-trimpath`, the commit's date) in a Go
container, as `tools/build-node-e2e.sh` does. The launcher prints its snapshot and warns
when a newer one exists. It does not update itself.

## Open questions

- **macOS Virtualization.framework**: `tools/boot-vz.sh` boots it through vfkit, and
  [container-host.md](container-host.md) is about using the image as a Podman VM there. qemu
  with HVF is enough to start with; VZ would be a second backend, added when qemu falls
  short.
- **WHPX** is the least tested accelerator. The first Windows run may find that it needs
  `kernel-irqchip=off` or a different `-cpu`.
- **Windows on arm64**: qemu's Windows builds are amd64, so this has no target yet.
- **Where the code lives**: `go/`, beside `python/`, with `go/cmd/kaetzli`.

## Order

1. Releases (`release.md`), since the launcher downloads from them.
2. The launcher for Linux, with `--print-argv` held to the harness.
3. macOS, then Windows, each with a CI job that starts a machine and logs in. GitHub's
   hosted macOS runners have no nested virtualisation, and Windows runners may not
   offer WHPX, so those jobs may have to boot with qemu's TCG, slowly.
