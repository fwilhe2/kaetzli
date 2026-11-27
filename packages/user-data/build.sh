# A script and its unit; nothing compiles. The source is packages/user-data/src, mounted
# read-only (LOCAL_SOURCE), so everything goes straight to DESTDIR.
install -D -m 755 user-data $ROOTFS/usr/lib/kaetzli/user-data

install -d $ROOTFS/usr/lib/systemd/system
cat > $ROOTFS/usr/lib/systemd/system/kaetzli-user-data.service <<'EOF'
[Unit]
Description=Run this instance's user data from the cloud's metadata service
Documentation=https://github.com/fwilhe2/kaetzli/blob/main/docs/guides/user-data.md
# Only where the command line names a source: the OpenStack disk image does, a VM booted
# from rootfs.erofs does not, and this unit is then skipped without a request sent.
ConditionKernelCommandLine=kaetzli.user-data
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
# The user data provisions the machine as root: writes /etc and units, starts services.
# Confining it would confine whatever it sets up, so there is no sandbox here.
ExecStart=/usr/lib/kaetzli/user-data
StateDirectory=kaetzli-user-data
StateDirectoryMode=0700
RuntimeDirectory=kaetzli-user-data
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=yes
# Gardener's user data pulls an image and starts a node agent: minutes, not seconds.
TimeoutStartSec=30min

[Install]
WantedBy=multi-user.target
EOF

# Enabled by shipping the .wants link: presets disable everything else
# (image/files/etc/systemd/system-preset). The condition keeps it off where unwanted.
install -d $ROOTFS/usr/lib/systemd/system/multi-user.target.wants
ln -s ../kaetzli-user-data.service \
    $ROOTFS/usr/lib/systemd/system/multi-user.target.wants/kaetzli-user-data.service
