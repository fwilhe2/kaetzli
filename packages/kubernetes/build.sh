# kubelet, kubeadm and kubectl, as upstream builds them (hack/lib/golang.sh): kubeadm and
# kubectl static (CGO_ENABLED=0, and -buildmode=exe over build-package.sh's pie), kubelet
# with cgo, against our glibc through the builder's CGO_CFLAGS/CGO_LDFLAGS like podman,
# and the tags upstream builds every release binary with — notest, and grpcnotrace,
# which lets the linker drop gRPC's tracing and is worth about a third of kubelet's size.
# Not selinux: the kernel has no SELinux.
# The rest of the release — the control plane, kube-proxy — runs as pods from
# registry.k8s.io images, which is the version skew kubeadm manages and not something a
# node ships.
#
# The tree is a Go workspace (go.work over staging/) with the workspace vendored, so the
# builder's -mod=vendor works as it is. Upstream's Makefile is not used: it wants git to
# compute the version and rebuilds its own tooling on the way. The version is what it
# would have computed, stamped the way hack/lib/version.sh does it, into both packages
# that report it — kubeadm refuses to work with a kubelet that cannot say what it is.
# gitCommit is left empty, as for podman: a release tarball has no .git.
major="${KAETZLI_VERSION%%.*}" minor="${KAETZLI_VERSION#*.}"; minor="${minor%%.*}"
build_date=$(date -u -d "@$SOURCE_DATE_EPOCH" +%Y-%m-%dT%H:%M:%SZ)
ldflags="-s -w"
for pkg in k8s.io/client-go/pkg/version k8s.io/component-base/version; do
    ldflags+=" -X $pkg.gitVersion=v$KAETZLI_VERSION -X $pkg.gitMajor=$major -X $pkg.gitMinor=$minor"
    ldflags+=" -X $pkg.gitTreeState=clean -X $pkg.buildDate=$build_date"
done

tags=notest,grpcnotrace
CGO_ENABLED=1 go build -tags "$tags" -ldflags "$ldflags" -o "$ROOTFS/usr/bin/kubelet" ./cmd/kubelet
for cmd in kubeadm kubectl; do
    CGO_ENABLED=0 go build -buildmode=exe -tags "$tags" -ldflags "$ldflags" -o "$ROOTFS/usr/bin/$cmd" "./cmd/$cmd"
done

# What kubeadm compares against the cluster it joins.
"$ROOTFS/usr/bin/kubelet" --version | grep -qx "Kubernetes v$KAETZLI_VERSION" || {
    echo "kubernetes: kubelet does not report v$KAETZLI_VERSION" >&2; exit 1; }
for cmd in kubeadm kubectl; do
    if readelf -d "$ROOTFS/usr/bin/$cmd" 2>/dev/null | grep -q NEEDED; then
        echo "kubernetes: $cmd is dynamically linked" >&2; exit 1
    fi
done

# ---------------------------------------------------------------------------------
# The scaffolding, installed with the binaries as every package here does (see
# packages/openssh). The extension is assembled from this tree as it stands: usr/ becomes
# the sysext, etc/ the confext (image/build-extension.sh).

# kubeadm's own unit and drop-in, folded into one: kubeadm writes the flags file and the
# configuration, then restarts this. Until it has, kubelet exits for want of a config and
# Restart= brings it back, which is upstream's behaviour too.
#
# A node's PKI and identity live under /var, and on a tmpfs /var it would come back from
# every reboot as a stranger to its own cluster. That is refused here rather than
# discovered there: the check exits 78 (EX_CONFIG), which RestartPreventExitStatus turns
# into a failed unit — a `degraded` boot that names the reason — instead of a restart loop.
install -d "$ROOTFS/usr/lib/systemd/system/multi-user.target.d"
cat > "$ROOTFS/usr/lib/systemd/system/kubelet.service" <<'UNIT'
[Unit]
Description=kubelet: The Kubernetes Node Agent
Documentation=https://kubernetes.io/docs/
Wants=network-online.target crio.service
After=network-online.target crio.service

[Service]
ExecStartPre=/usr/bin/bash -c '[ "$(stat -f -c %%T /var)" != tmpfs ] || { echo "kubelet: /var is a tmpfs, and a node needs a disk for it (docs/guides/sysext.md)" >&2; exit 78; }'
Environment="KUBELET_KUBECONFIG_ARGS=--bootstrap-kubeconfig=/etc/kubernetes/bootstrap-kubelet.conf --kubeconfig=/etc/kubernetes/kubelet.conf"
Environment="KUBELET_CONFIG_ARGS=--config=/var/lib/kubelet/config.yaml"
EnvironmentFile=-/var/lib/kubelet/kubeadm-flags.env
ExecStart=/usr/bin/kubelet $KUBELET_KUBECONFIG_ARGS $KUBELET_CONFIG_ARGS $KUBELET_KUBEADM_ARGS $KUBELET_EXTRA_ARGS
Restart=always
RestartSec=5
RestartPreventExitStatus=78
UNIT

# Started by Upholds=, not a .wants link: the extension is merged during sysinit, after
# the boot transaction was computed, and a .wants link that appears then starts nothing
# until the next boot. test/sysext.py shows both.
cat > "$ROOTFS/usr/lib/systemd/system/multi-user.target.d/10-kubelet.conf" <<'UNIT'
[Unit]
Upholds=kubelet.service
UNIT

# kubeadm hardcodes /etc/kubernetes for kubeconfigs, PKI and static pod manifests. That
# is node state, so it lives on /var and /etc carries a link to it, in the confext — the
# same move as resolv.conf into /run. Nothing is made writable.
install -d "$ROOTFS/etc" "$ROOTFS/usr/lib/tmpfiles.d"
ln -sfn /var/lib/kubernetes "$ROOTFS/etc/kubernetes"
cat > "$ROOTFS/usr/lib/tmpfiles.d/kubernetes.conf" <<'CONF'
d /var/lib/kubernetes 0755 root root -
CONF

# FlexVolume's default directory, which kubelet and the controller manager's static pod
# both want to exist and neither needs to write. Empty, and staying so: FlexVolume is
# deprecated for CSI, whose drivers are pods, and on an immutable /usr a plugin installed
# by copying a binary there is exactly what is not supposed to happen.
install -d "$ROOTFS/usr/libexec/kubernetes/kubelet-plugins/volume/exec"
