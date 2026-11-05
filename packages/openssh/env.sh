# The SSH server, and the first thing this image ever *listens* on — which is a change of
# security posture rather than merely another package, and is the reason this one is worth
# arguing about before it is worth packaging.
#
# A serial console is enough to debug a boot and not enough to work in: no scp, no second
# terminal, no port forward, and nothing a tool on the host can drive. Every VM workflow
# that is not a console — Lima (docs/proposals/image-variants.md), Vagrant, an IDE's remote mode,
# tools/ssh.sh here — boots the guest and then does everything else over ssh, so a VM
# somebody actually works in needs a server before it needs most of what it already has.
#
# There is no second candidate. Dropbear and tinyssh are the alternatives and neither is
# close: Lima writes OpenSSH configuration verbatim into its own ssh_config (multiplexing
# through ControlMaster/ControlPath, reverse forwards, a pile of `-o` settings), the
# reverse-sshfs mount is an OpenSSH client talking to an OpenSSH server, and every
# distribution's `ssh` is this one. It is C, and it links only libraries the image already
# ships: libcrypto from openssl, libz from zlib, libpam from pam and libcrypt from
# libxcrypt.
#
# It ships no interpreted helper either (constraint 5): the one shell script in the
# tarball is contrib/ssh-copy-id, which `make install` does not install.
#
# The OpenSSL coupling curl's env.sh describes applies here too and for the same reason —
# this compiles against sid's libssl-dev headers and asks libcrypto.so.3 for its symbols
# at runtime, which packages/openssl answers only while it stays on a 3.x release.
VERSION="10.6p2"
PACKAGE="openssh-${VERSION}"
TARBALL="$PACKAGE.tar.gz"
URL="https://cdn.openbsd.org/pub/OpenBSD/OpenSSH/portable/${TARBALL}"
SHA256="9ae44872090cf2f06b3cf9ff147e7f16f309671340763ed2f99248398ae29d5b"
# Read off the tarball's LICENCE, which enumerates them rather than naming one: the
# OpenSSH licence proper for Tatu Ylonen's original code (1 and 2), ssh-keyscan's own
# terms (3), the public-domain Rijndael implementation (4), 3-clause BSD for the Berkeley
# code (5), 2-clause BSD for everything else (6), and 3-clause BSD, ISC and X11 across
# openbsd-compat (8). All of it is BSD-ish or freer — the file's own opening summary — and
# openssh has been in Debian main since there was a Debian main.
LICENSE="SSH-OpenSSH AND ssh-keyscan AND BSD-2-Clause AND BSD-3-Clause AND ISC AND X11 AND LicenseRef-PublicDomain"
CPE="cpe:2.3:a:openbsd:openssh:${VERSION}:*:*:*:*:*:*:*"
# Portable OpenSSH numbers itself <upstream>p<portable>, so the version is 10.5p1 and not
# 10.5. tools/upstream.py understands that suffix; nothing else here has one, which is why
# the pattern in that script had to learn it.
