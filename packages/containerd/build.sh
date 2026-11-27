# containerd, ctr and the runc v2 shim, built with go directly rather than through the
# Makefile, whose GO_GCFLAGS and static-build switches would fight builder/build-package.sh's
# GOFLAGS. The tags take out what the image has no use for:
#
#   no_btrfs, no_zfs, no_devmapper  snapshotters for storage the VM does not have; the
#                                   snapshotter is overlayfs. btrfs is also the only cgo user.
#   no_tracing                      OpenTelemetry export, which needs a collector
#   urfave_cli_no_docs              no man-page generator in the CLI
#
# The shim is static and not PIE, as upstream builds it: it is started for every pod and
# re-executes itself, and a static binary is what upstream supports for that (its
# Makefile's comment, golang/go#17789). CGO_ENABLED=0, so it links nothing of ours.
pkg=github.com/containerd/containerd/v2
ldflags="-s -w -X $pkg/version.Version=v$KAETZLI_VERSION -X $pkg/version.Revision= -X $pkg/version.Package=github.com/containerd/containerd"
tags="urfave_cli_no_docs no_btrfs no_zfs no_devmapper no_tracing"

go build -tags "$tags" -ldflags "$ldflags" -o "$ROOTFS/usr/bin/containerd" ./cmd/containerd
go build -tags "$tags" -ldflags "$ldflags" -o "$ROOTFS/usr/bin/ctr" ./cmd/ctr
CGO_ENABLED=0 go build -buildmode=exe -tags "$tags no_grpc" -ldflags "$ldflags" \
    -o "$ROOTFS/usr/bin/containerd-shim-runc-v2" ./cmd/containerd-shim-runc-v2

# ---------------------------------------------------------------------------------
# The unit, as upstream ships it but for the path and the modprobe (no modules here:
# overlayfs is built in). Started by Upholds=, since a .wants link merged during sysinit
# would start nothing (docs/guides/sysext.md) — from basic.target rather than
# multi-user.target, so it is up before anything ordered ahead of multi-user.target needs
# it: kaetzli-user-data.service, whose user data on a Gardener node pulls the node agent
# with ctr (the drop-in below), and kubelet. Gardener's OperatingSystemConfig names
# containerd.service with drop-ins only and expects the OS to have it.
install -d "$ROOTFS/usr/lib/systemd/system/basic.target.d" \
    "$ROOTFS/usr/lib/systemd/system/kaetzli-user-data.service.d"
cat > "$ROOTFS/usr/lib/systemd/system/containerd.service" <<'UNIT'
[Unit]
Description=containerd container runtime
Documentation=https://containerd.io
After=network.target dbus.service
Before=kubelet.service

[Service]
ExecStart=/usr/bin/containerd
Type=notify
Delegate=yes
KillMode=process
Restart=always
RestartSec=5
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
OOMScoreAdjust=-999

[Install]
WantedBy=multi-user.target
UNIT
cat > "$ROOTFS/usr/lib/systemd/system/basic.target.d/10-containerd.conf" <<'UNIT'
[Unit]
Upholds=containerd.service
UNIT
# Ordering only: the boot transaction that started the user data was computed before the
# extension merged, so a Wants= here would pull nothing in; Upholds= above starts it.
cat > "$ROOTFS/usr/lib/systemd/system/kaetzli-user-data.service.d/10-containerd.conf" <<'UNIT'
[Unit]
After=containerd.service
UNIT

# containerd's own defaults, with the runc v2 shim running the base's crun (which takes
# runc's command line), the systemd cgroup driver, the base spec below, the opt plugin's
# directory in /var rather than the read-only /opt/containerd, and the CNI plugins
# cni-plugins ships in /usr/libexec/cni ahead of /opt/cni/bin, where a cluster's CNI
# DaemonSet copies its own. Goes to the extension's confext.
# Gardener's node agent keeps a config.toml it finds and patches only the sandbox image,
# CNI directories, SystemdCgroup and the OSC's plugin entries into it (pkg/nodeagent/
# controller/operatingsystemconfig/containerd_config.go), so BinaryName survives; without
# this file it writes `containerd config default`, whose shim looks for runc.
install -d "$ROOTFS/etc/containerd"
"$ROOTFS/usr/bin/containerd" config default > /tmp/containerd-config.toml

# The base OCI spec of every pod container, for one annotation. With the systemd cgroup
# driver crun puts a container's processes in a `container` subgroup below the cgroup
# containerd names, unless run.oci.systemd.subgroup says otherwise, and kubelet's
# cAdvisor counts that cgroup's processes: 0, which the node e2e Summary API test fails
# (CRI-O set the same annotation by default). runc has no subgroup, so this is how a
# containerd pod is laid out anywhere else. containerd's CRI has no default annotations,
# only a base spec: ctr's (the same defaults the CRI starts from) without what the CRI
# would otherwise clear when there is no base spec (WithoutDefaultSecuritySettings:
# AppArmor, seccomp, rlimits), so the rest of a pod's spec is unchanged.
"$ROOTFS/usr/bin/ctr" oci spec > /tmp/containerd-base-spec.json
python3 - /tmp/containerd-base-spec.json "$ROOTFS/etc/containerd/cri-base.json" <<'PY2'
import json, sys
spec = json.load(open(sys.argv[1]))
spec["process"].pop("apparmorProfile", None)
spec["process"].pop("rlimits", None)
spec.get("linux", {}).pop("seccomp", None)
spec.setdefault("annotations", {})["run.oci.systemd.subgroup"] = ""
with open(sys.argv[2], "w") as f:
    json.dump(spec, f, indent=2, sort_keys=True)
    f.write("\n")
PY2
# The table, at whatever depth the TOML encoder indents it.
runc_options="^ *\[plugins\.'io\.containerd\.cri\.v1\.runtime'\.containerd\.runtimes\.runc\.options\]$"
grep -qE "$runc_options" /tmp/containerd-config.toml || {
    echo "containerd: no runc options table in its default config" >&2; exit 1; }
runc_table="^ *\[plugins\.'io\.containerd\.cri\.v1\.runtime'\.containerd\.runtimes\.runc\]$"
sed -E "/$runc_table/,/^ *\[/{
    s#^( *)base_runtime_spec = .*#\1base_runtime_spec = '/etc/containerd/cri-base.json'#
}
/$runc_options/,/^ *\[/{
    s#^( *)BinaryName = .*#\1BinaryName = '/usr/bin/crun'#
    s#^( *)SystemdCgroup = .*#\1SystemdCgroup = true#
}
/^ *\[plugins\.'io\.containerd\.cri\.v1\.runtime'\.cni\]$/,/^ *\[/{
    s#^( *)bin_dirs = .*#\1bin_dirs = ['/usr/libexec/cni', '/opt/cni/bin']#
}
/^ *\[plugins\.'io\.containerd\.internal\.v1\.opt'\]$/,/^ *\[/{
    s#^( *)path = .*#\1path = '/var/lib/containerd/opt'#
}" /tmp/containerd-config.toml > "$ROOTFS/etc/containerd/config.toml"
grep -q "BinaryName = '/usr/bin/crun'" "$ROOTFS/etc/containerd/config.toml" \
    && grep -q "SystemdCgroup = true" "$ROOTFS/etc/containerd/config.toml" \
    && grep -q "path = '/var/lib/containerd/opt'" "$ROOTFS/etc/containerd/config.toml" \
    && grep -q "bin_dirs = \['/usr/libexec/cni', '/opt/cni/bin'\]" "$ROOTFS/etc/containerd/config.toml" \
    && grep -q "base_runtime_spec = '/etc/containerd/cri-base.json'" "$ROOTFS/etc/containerd/config.toml" || {
    echo "containerd: could not set BinaryName, SystemdCgroup, the opt path, the CNI directories and the base spec in config.toml" >&2
    exit 1
}
