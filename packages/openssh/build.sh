# The service, its account, its PAM stack and its config are installed from here, not from
# image/files, so they ship exactly when sshd does: a unit naming a missing binary is a
# degraded boot.
#
# --with-pam: pam_systemd registers the session with logind.
# --with-sandbox=seccomp_filter: what configure picks anyway, named so a lost header
#   fails the build instead of silently downgrading the sandbox. Raw seccomp(2), no library.
# --with-default-path/--with-superuser-path: the compiled default names /usr/sbin and
#   /sbin, which merged /usr makes the same directory twice.
# --without/--disable: libraries and providers the image never has, named so a line added
#   to builder/deps.txt for another package cannot pull them into sshd. No utmp, wtmp or
#   lastlog: nothing here creates or reads them.
# --without-openssl-header-check: the check runs a test program on the builder's
#   libcrypto, not the one the image ships. sshd checks the real pair at every start.
# --disable-strip: the image assembly strips; the staging tree keeps symbols for debugging.
./configure \
    --prefix=/usr \
    --sysconfdir=/etc/ssh \
    --libexecdir=/usr/libexec \
    --with-pid-dir=/run \
    --with-privsep-path=/usr/share/empty.sshd \
    --with-privsep-user=sshd \
    --with-default-path=/usr/local/bin:/usr/bin \
    --with-superuser-path=/usr/local/bin:/usr/bin \
    --with-pam \
    --with-zlib \
    --without-openssl-header-check \
    --with-sandbox=seccomp_filter \
    --without-selinux \
    --without-audit \
    --without-libedit \
    --without-ldns \
    --without-wtmpdb \
    --without-kerberos5 \
    --without-ssl-engine \
    --without-security-key-builtin \
    --disable-security-key \
    --disable-pkcs11 \
    --disable-lastlog \
    --disable-utmp \
    --disable-utmpx \
    --disable-wtmp \
    --disable-wtmpx \
    --disable-strip

make

# install-nokeys: no host keys in an image built from a public repository.
make install-nokeys DESTDIR=$ROOTFS

# ssh-keysign is setuid and only for HostbasedAuthentication; the pkcs11 and sk helpers
# are stubs for what is configured out. The agent belongs where the keys are (`ssh -A`
# forwards it). moduli only serves finite-field DH, which OpenSSH 10's server no longer
# offers by default.
drop_installed usr/libexec/ssh-keysign usr/libexec/ssh-pkcs11-helper \
    usr/libexec/ssh-sk-helper ssh-agent ssh-add etc/ssh/moduli

# ---------------------------------------------------------------------------------
# The server's configuration: the few lines that are not defaults. Each user's own
# ~/.ssh/authorized_keys, and the key the VMM handed this boot (test/qemu-lib.sh), never
# from a copy in a home: tmpfiles writes those only if missing, and /home and /root are on
# the /var disk when there is one, so a copy would keep the first boot's key forever. sshd
# reads the file as the target user, so root's is the credential where PID 1 put it, and
# every other user's a copy in /run (image/files/etc/tmpfiles.d/kaetzli-home.conf).
cat > $ROOTFS/etc/ssh/sshd_config <<'EOF'
# Drop-ins first: in sshd_config the first setting of an option wins.
Include /etc/ssh/sshd_config.d/*.conf

# PAM registers the session with systemd-logind. The compiled default is no.
UsePAM yes

# Host keys are machine state, made at boot (sshd-keygen.service); / is read-only.
HostKey /var/lib/sshd/ssh_host_rsa_key
HostKey /var/lib/sshd/ssh_host_ecdsa_key
HostKey /var/lib/sshd/ssh_host_ed25519_key

# Keys only. With PAM on, keyboard-interactive would be a second password path.
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no

Subsystem sftp /usr/libexec/sftp-server

# The key handed to this boot as a credential. See packages/openssh/build.sh.
AuthorizedKeysFile .ssh/authorized_keys /run/ssh-authorized-keys/%u
Match User root
    AuthorizedKeysFile .ssh/authorized_keys /run/credentials/@system/ssh.authorized_keys.root
EOF

# ---------------------------------------------------------------------------------
# Type=exec: sshd has no sd_notify upstream. KillMode=process, or restarting sshd kills
# the session of whoever restarted it.
install -d $ROOTFS/usr/lib/systemd/system
cat > $ROOTFS/usr/lib/systemd/system/sshd.service <<'EOF'
[Unit]
Description=OpenSSH server daemon
Documentation=man:sshd(8) man:sshd_config(5)
After=network.target sshd-keygen.service
Wants=sshd-keygen.service

[Service]
Type=exec
ExecStart=/usr/bin/sshd -D -e
ExecReload=/usr/bin/kill -HUP $MAINPID
KillMode=process
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF

# Host keys, at boot, into /var/lib/sshd (ssh-keygen -A only writes under /etc). Runs when
# any key is missing (`|` ORs the conditions) and makes only the missing ones.
cat > $ROOTFS/usr/lib/systemd/system/sshd-keygen.service <<'EOF'
[Unit]
Description=Generate the OpenSSH host keys that are missing
Documentation=man:ssh-keygen(1)
ConditionPathExists=|!/var/lib/sshd/ssh_host_rsa_key
ConditionPathExists=|!/var/lib/sshd/ssh_host_ecdsa_key
ConditionPathExists=|!/var/lib/sshd/ssh_host_ed25519_key

[Service]
Type=oneshot
RemainAfterExit=yes
StateDirectory=sshd
StateDirectoryMode=0700
ExecStart=/usr/bin/bash -c 'for t in rsa ecdsa ed25519; do k=/var/lib/sshd/ssh_host_$${t}_key; [ -e "$$k" ] || /usr/bin/ssh-keygen -q -t "$$t" -N "" -C "" -f "$$k" || exit; done'
EOF

# Enabled by shipping the .wants link: presets disable everything else.
install -d $ROOTFS/usr/lib/systemd/system/multi-user.target.wants
ln -sfn ../sshd.service \
    $ROOTFS/usr/lib/systemd/system/multi-user.target.wants/sshd.service

# ---------------------------------------------------------------------------------
# The privilege separation account, created by sysusers at image assembly. It chroots into
# /usr/share/empty.sshd rather than /var/empty, since /var starts empty.
install -d $ROOTFS/usr/lib/sysusers.d
cat > $ROOTFS/usr/lib/sysusers.d/sshd.conf <<'EOF'
u sshd - "OpenSSH privilege separation" /usr/share/empty.sshd -
EOF

# ---------------------------------------------------------------------------------
# A PAM file of its own is required: pam.d/other is pam_deny.
install -d $ROOTFS/etc/pam.d
cat > $ROOTFS/etc/pam.d/sshd <<'EOF'
auth       required   pam_unix.so
account    required   pam_unix.so
session    required   pam_unix.so
# Optional: a broken pam_systemd.so should cost session tracking, not the login.
session    optional   pam_systemd.so
# The image's notice (image/build-rootfs.sh), as login shows it on the console. Only
# that file: sshd's PrintMotd shows /etc/motd after it. Interactive sessions only.
session    optional   pam_motd.so motd=/usr/share/misc/motd
password   required   pam_unix.so
EOF
