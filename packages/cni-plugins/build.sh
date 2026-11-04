# Four of the reference plugins, into /usr/libexec/cni: the FHS place for programs other
# programs run, where CRI-O's plugin_dirs points first (docs/guides/sysext.md). /opt/cni/bin is
# left to whatever a cluster's CNI DaemonSet copies in. Static Go, as upstream builds them
# (-buildmode=exe over the pie that builder/build-package.sh sets for cgo programs).
for plugin in main/bridge main/loopback ipam/host-local meta/portmap; do
    CGO_ENABLED=0 go build -buildmode=exe \
        -ldflags "-s -w -X github.com/containernetworking/plugins/pkg/utils/buildversion.BuildVersion=v$KAETZLI_VERSION" \
        -o "$ROOTFS/usr/libexec/cni/${plugin#*/}" "./plugins/$plugin"
done

# The two paths a cluster's CNI DaemonSet writes — its network config into /etc/cni/net.d,
# its plugin binaries into /opt/cni/bin — are links into /var, so stock manifests work
# unmodified on a read-only /etc and /opt (docs/guides/sysext.md). The /opt link is in the
# sysext, the /etc one in the confext; tmpfiles makes what they point at.
install -d "$ROOTFS/opt/cni" "$ROOTFS/etc/cni" "$ROOTFS/usr/lib/tmpfiles.d"
ln -sfn /var/lib/cni/bin "$ROOTFS/opt/cni/bin"
ln -sfn /var/lib/cni/net.d "$ROOTFS/etc/cni/net.d"
cat > "$ROOTFS/usr/lib/tmpfiles.d/cni.conf" <<'CONF'
d /var/lib/cni/bin 0755 root root -
d /var/lib/cni/net.d 0755 root root -
CONF
