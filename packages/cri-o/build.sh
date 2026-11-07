# crio and pinns. The tags are named rather than probed, as for podman: the Makefile's
# default runs a hack/ script per tag that asks what the builder has installed.
#
#   seccomp                      libseccomp, for the default profile CRI-O hands crun
#   containers_image_openpgp     signatures in pure Go, not through gpgme (as podman)
#   exclude_graphdriver_btrfs    storage is overlay; no libbtrfs
#   btrfs_noversion              the same, for the version probe that would want it
#   containers_image_ostree_stub no ostree transport
#
# No selinux or apparmor tag: the kernel has neither LSM. BUILD_COMMIT is empty, there
# being no .git in a release tarball; BUILD_DATE comes from SOURCE_DATE_EPOCH.
buildtags="seccomp containers_image_openpgp exclude_graphdriver_btrfs btrfs_noversion containers_image_ostree_stub"
make BUILDTAGS="$buildtags" BUILD_COMMIT= SHRINKFLAGS="-s -w" bin/crio

# pinns pins the namespaces of a pod sandbox. Its Makefile's `CFLAGS ?=` asks for
# -static and -Werror; the exported CFLAGS replace that with the sysroot's, so it links
# against our glibc like everything else here, and C99 is the one part kept.
make -C pinns CFLAGS="$CFLAGS -std=c99" LDFLAGS="$LDFLAGS"

install -D -m 755 bin/crio "$ROOTFS/usr/bin/crio"
install -D -m 755 bin/pinns "$ROOTFS/usr/bin/pinns"

readelf -d "$ROOTFS/usr/bin/crio" | grep -q 'NEEDED.*libseccomp' || {
    echo "cri-o: crio is not linked against libseccomp" >&2; exit 1; }

# ---------------------------------------------------------------------------------
# The scaffolding (see packages/kubernetes): the unit, started by Upholds= since a .wants
# link merged during sysinit would start nothing, and the one piece of configuration
# CRI-O's compiled-in defaults do not cover, which goes to the confext.
install -d "$ROOTFS/usr/lib/systemd/system/multi-user.target.d"
cat > "$ROOTFS/usr/lib/systemd/system/crio.service" <<'UNIT'
[Unit]
Description=Container Runtime Interface for OCI (CRI-O)
Documentation=https://github.com/cri-o/cri-o
Wants=network-online.target
After=network-online.target
Before=kubelet.service

[Service]
Type=notify
ExecStart=/usr/bin/crio
ExecReload=/usr/bin/kill -s HUP $MAINPID
TasksMax=infinity
LimitNOFILE=1048576
OOMScoreAdjust=-999
TimeoutStartSec=0
Restart=on-failure
RestartSec=10
UNIT
cat > "$ROOTFS/usr/lib/systemd/system/multi-user.target.d/10-crio.conf" <<'UNIT'
[Unit]
Upholds=crio.service
UNIT

# CRI-O on the image's runtime: crun and conmon from the base's /usr/bin, the policy and
# registries from its /etc/containers (image/files). CNI plugins: the extension's own
# first, then wherever a cluster's CNI DaemonSet copies its binaries. conmon and pinns
# are found on $PATH; the storage driver is overlay by default.
install -d "$ROOTFS/etc/crio/crio.conf.d"
# Per-namespace signature policies, which an administrator would supply. CRI-O insists on
# creating the directory at start and refuses to run when /etc is read-only, so it ships,
# empty — given what it looks for, like FlexVolume's directory in packages/kubernetes.
install -d "$ROOTFS/etc/crio/policies"
# Per-namespace registry credentials, which CRI-O's credential provider writes at runtime
# and CRI-O also refuses to start without a directory for. Written, so a link out of /etc
# like /etc/kubernetes — but into /run, being short-lived secrets that have no business
# surviving a reboot on the /var disk.
install -d "$ROOTFS/usr/lib/tmpfiles.d"
ln -sfn /run/crio/auth "$ROOTFS/etc/crio/auth"
cat > "$ROOTFS/usr/lib/tmpfiles.d/crio.conf" <<'CONF'
d /run/crio/auth 0700 root root -
CONF
# Two settings are for kubelet's stats, which come from the cAdvisor built into it and
# which test/node-conformance.py's Summary API test checks. cAdvisor reads a pod's network
# counters through its infra (pause) process, which CRI-O by default does not start; and
# it counts a container's processes in the cgroup CRI-O names, where crun on cgroup v2
# puts them one level below, in a `container` subgroup. Without the two, /stats/summary
# has no pod network stats and a process count of 0. Both are how containerd and runc lay
# a pod out anyway. The other way round — kubelet asking CRI-O instead, the
# PodAndContainerStatsFromCRI gate — is beta and off in 1.37, and fails the same test on
# more fields (no PSI); worth trying again when it is on by default.
cat > "$ROOTFS/etc/crio/crio.conf.d/10-kaetzli.conf" <<'CONF'
[crio.runtime]
default_runtime = "crun"
drop_infra_ctr = false

[crio.runtime.runtimes.crun]
runtime_path = "/usr/bin/crun"
runtime_root = "/run/crun"
default_annotations = { "run.oci.systemd.subgroup" = "" }

[crio.image]
signature_policy = "/etc/containers/policy.json"

[crio.network]
plugin_dirs = ["/usr/libexec/cni", "/opt/cni/bin"]
CONF
