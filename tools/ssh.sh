#!/usr/bin/env bash
# Log into the guest tools/boot-qemu.py (or boot-firecracker.sh, boot-vz.sh) is running,
# as `user`, without a password:
#
#     ./tools/ssh.sh                      # interactive shell
#     ./tools/ssh.sh podman ps            # one command
#     ./tools/ssh.sh --root               # as root
#     ./tools/ssh.sh --root journalctl -b # ... and one command as root
#     ./tools/ssh.sh -L 8080:localhost:80 # any ssh option, as long as it comes before a command
#
# The key, the port and every option are in the ssh_config qemu_ssh_setup (test/qemu-lib.sh)
# wrote for this boot, so this is `ssh -F $SSH_DIR/config kaetzli` and nothing more — scp,
# sftp and rsync -e take the same -F. The key exists only while that guest runs; there is
# nothing to set up and nothing left behind.
#
# `user` by default, because the unprivileged account is the one to work in, and because
# a login of its own — a logind session, /run/user/1000 and a user manager — is what
# rootless podman needs and what `su user` from a root session never gives it: su keeps
# root's $XDG_RUNTIME_DIR, and pam_systemd will not open a session inside one. The key is
# the same one either way; image/files/etc/tmpfiles.d/kaetzli-home.conf copies it into
# user's authorized_keys. --root, which has to come first, logs in as root instead.
#
# Overrides:
#   SSH_DIR   where the boot script put the key and config   (default output/ssh)
set -euo pipefail

cd "$(dirname "$0")/.."

SSH_DIR="${SSH_DIR:-output/ssh}"
if [ ! -f "$SSH_DIR/config" ]; then
    echo "error: no $SSH_DIR/config — is a guest running? Start one with ./tools/boot-qemu.py (or boot-firecracker.sh, boot-vz.sh)" >&2
    echo "       (a machine started with ./tools/vm.sh is ./tools/vm.sh ssh [name])" >&2
    exit 1
fi

login=user
if [ "${1:-}" = --root ]; then
    login=root
    shift
fi

exec ssh -F "$SSH_DIR/config" -l "$login" kaetzli "$@"
