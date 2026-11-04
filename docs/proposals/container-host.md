# Linux VM as a macOS container host

**Status: proposal.** This project does not currently provide a macOS container-host
launcher or Podman machine integration. The idea is to run this Linux image in a VM on
Apple silicon and connect host container tools to the guest's Podman service.

## Existing pieces

The image already has a read-only dm-verity root, persistent `/var` support, Podman and
rootless container support in extensions, and SSH access through boot credentials. The
kernel includes the virtiofs, vsock, balloon, RNG, and virtio-console features needed for
the proposed VM path.

`tools/boot-vz.sh` is a manual vfkit boot path for Apple silicon. It is not a lifecycle
manager and does not provide the host socket, port forwarding, or file-sharing experience
expected from a container host.

## Work required

1. **Lifecycle tool:** create, start, stop, inspect, and update named VMs while preserving
   each VM's `/var` disk and machine identity. The existing `tools/vm.sh` covers QEMU on
   Linux, not the macOS workflow.
2. **Host API connection:** expose the guest's rootless Podman socket to Podman on the Mac.
   A forwarded SSH connection is a simple first step; vsock may avoid guest network
   exposure.
3. **Ports and files:** forward dynamically published container ports to the Mac and mount
   host directories into the guest, likely through vfkit's virtiofs support.
4. **Credentials and identity:** provide a key for the unprivileged `user` account and
   decide how host file ownership maps to the guest account.
5. **Suspend and resume:** verify guest clock correction and persistent disk behavior after
   the Mac sleeps.
6. **Updates:** verify downloaded images before switching a VM to a new root image.

## Security and scope

The host controls the VM's kernel command line and attached devices. A verified root image
protects against accidental or unauthorized changes to the image file, but does not
authenticate who supplied that file. CI attests every file it builds on main (Sigstore,
`gh attestation verify`); a launcher should verify that before using an image.

This project does not aim to emulate Podman machine's Ignition configuration or user model.
A first implementation should provide its own small launcher, use the existing image and
extension formats, and avoid adding host integration packages to the guest unless a
specific feature requires them.

## Suggested order

Start with a Linux-hosted launcher and a forwarded Podman socket, then add the vfkit
backend, virtiofs mounts, dynamic port forwarding, suspend/resume handling, and signed
updates. Keep this work separate from the generic image build so ordinary VM users do not
pay for host-specific behavior.
