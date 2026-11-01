# defconfig + kvm_guest.config, then fragments that say what the kernel is for (container,
# k8s, guest, rootfs, kspp, trace), one that takes defconfig's hardware back out (vm), and
# one that makes both arches agree (parity). Built natively, so `make defconfig` picks the
# arch from `uname -m`. The fragments are heredocs because only this file is bind-mounted
# into the builder. kvm_guest.config's x86-only symbols (PARAVIRT and friends) are skipped
# with a warning on arm64, which is harmless.
#
# Reproducible: without these kbuild stamps date, user, host and a per-tree build counter
# into `uname -v`. SOURCE_DATE_EPOCH comes from build.sh.
export KBUILD_BUILD_TIMESTAMP="$(date -u -d "@$SOURCE_DATE_EPOCH")"
export KBUILD_BUILD_USER=kaetzli KBUILD_BUILD_HOST=kaetzli KBUILD_BUILD_VERSION=1

make defconfig
make kvm_guest.config

# What a container runtime needs. defconfig stops at CGROUPS, the basic namespaces and
# SECCOMP_FILTER. `make <name>.config` merges a fragment and re-runs olddefconfig, which
# drops anything whose dependencies are unmet in silence — the check after vm.config is
# what notices.
cat > kernel/configs/container.config <<'EOF'
# User namespaces, and memory.max (any memory limit fails without MEMCG).
CONFIG_USER_NS=y
CONFIG_MEMCG=y

# cpu.max (docker --cpus, every Kubernetes CPU limit) and io.max's bandwidth limits.
CONFIG_CFS_BANDWIDTH=y
CONFIG_BLK_DEV_THROTTLING=y

# The cgroup v2 device controller is a BPF program.
CONFIG_BPF_SYSCALL=y
CONFIG_CGROUP_BPF=y

# systemd's BPF sandboxing (loaded through libbpf).
# BPF_JIT: BPF_LSM depends on it.
# FTRACE: BPF_LSM also needs BPF_EVENTS, which is `default y` behind the probe event
#   types inside the FTRACE menu — and arm64's defconfig turns FTRACE off.
# BPF_LSM: RestrictFileSystems= and nsresourced. CONFIG_LSM already lists "bpf".
# SECURITYFS: systemd reads /sys/kernel/security/lsm to decide whether bpf-lsm exists.
CONFIG_BPF_JIT=y
CONFIG_FTRACE=y
CONFIG_BPF_LSM=y
CONFIG_SECURITYFS=y

# An LSM program names the kernel function it hooks, which needs /sys/kernel/btf/vmlinux
# ("bpf-restrict-fs: ... No such process" at boot without it). DEBUG_INFO_BTF lives inside
# `if DEBUG_INFO`, which only the "Debug information" choice selects, so the choice has to
# be settled first. Expensive: full debug info plus pahole (dwarves, builder/deps.txt);
# only the .BTF section ships.
CONFIG_DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT=y
# CONFIG_DEBUG_INFO_REDUCED is not set
CONFIG_DEBUG_INFO_BTF=y

# Image layers.
CONFIG_OVERLAY_FS=y

# Foreign-arch containers: `podman run --platform` through qemu-user, or Rosetta.
CONFIG_BINFMT_MISC=y

# Container networking: veth into a bridge, tun for userspace networking.
CONFIG_VETH=y
CONFIG_BRIDGE=y
CONFIG_TUN=y

# nftables and NAT for published ports; nftables hides behind NETFILTER_ADVANCED.
CONFIG_NETFILTER_ADVANCED=y
CONFIG_BRIDGE_NETFILTER=y
CONFIG_NF_TABLES=y
CONFIG_NF_TABLES_INET=y
CONFIG_NFT_NAT=y
CONFIG_NFT_MASQ=y
CONFIG_NETFILTER_XT_MATCH_ADDRTYPE=y

# What engines' nftables rules use. dockerd: `ct state`, `fib daddr type local`, for ip
# and ip6.
CONFIG_NFT_CT=y
CONFIG_NFT_FIB_IPV4=y
CONFIG_NFT_FIB_IPV6=y
# netavark: `fib` inside an `inet` table ("No such file or directory" without it), and
# `ct mark set` for published ports ("Operation not supported").
CONFIG_NFT_FIB_INET=y
CONFIG_NF_CONNTRACK_MARK=y

# iptables-nft: matches and targets it cannot express natively run as x_tables
# extensions through NFT_COMPAT ("revision 0 not supported" without one). These are
# Docker's iptables set, named even where defconfig has them so the check holds them.
CONFIG_NFT_COMPAT=y
CONFIG_NETFILTER_XT_MATCH_CONNTRACK=y
CONFIG_NETFILTER_XT_MARK=y
CONFIG_NETFILTER_XT_TARGET_MASQUERADE=y
CONFIG_NETFILTER_XT_NAT=y
EOF
make container.config

# What a Kubernetes node needs on top (docs/guides/sysext.md). With modules off, a feature a pod
# expects of the host kernel is in here or nowhere.
#   NFT_NUMGEN, NFT_REJECT  kube-proxy's nftables mode: endpoint choice, no-endpoint reject
#   NF_CT_NETLINK  kube-proxy flushes stale UDP conntrack (else DNS to a restarted CoreDNS
#                  blackholes)
#   VXLAN, NFT_REDIR, NF_TABLES_NETDEV  CNI overlays and dataplanes (flannel, Calico, Cilium)
#   NETFILTER_XT_TARGET_REDIRECT, NETFILTER_XT_MATCH_COMMENT  kubeadm preflight refuses a
#                  node without them (it reads /usr/lib/modules/<release>/config, below)
#   DUMMY          dummy interfaces: kube-proxy's IPVS mode (kube-ipvs0), NodeLocal
#                  DNSCache, and node conformance's privileged-pod test, which makes one
cat > kernel/configs/k8s.config <<'EOF'
CONFIG_NFT_NUMGEN=y
CONFIG_NFT_REJECT=y
CONFIG_NF_CT_NETLINK=y
CONFIG_VXLAN=y
CONFIG_NFT_REDIR=y
CONFIG_NF_TABLES_NETDEV=y
CONFIG_NETFILTER_XT_TARGET_REDIRECT=y
CONFIG_NETFILTER_XT_MATCH_COMMENT=y
CONFIG_DUMMY=y
EOF
make k8s.config


# The VMMs' virtual hardware, beyond kvm_guest.config's qemu PCI devices.
#   Firecracker: no PCI, virtio-mmio devices named on the command line, PVH boot of a
#     vmlinux, an 8250 console (device-tree on arm64), Ctrl-Alt-Del through i8042 on x86.
#   Virtualization.framework: hvc0, virtiofs (which replaces 9p here), vsock.
#   All: balloon, virtio-rng, and VMGENID, so two VMs restored from one snapshot reseed
#     their RNG instead of sharing a key stream.
#   qemu: FW_CFG_SYSFS, qemu's fw_cfg entries under /sys/firmware/qemu_fw_cfg. systemd
#     imports credentials from opt/io.systemd.credentials/ there (qemu's -fw_cfg,
#     libvirt's <sysinfo type="fwcfg">), the channel for anything that should not be on
#     the command line: too big for its 2048 bytes, or a secret, since /proc/cmdline is
#     world-readable. Unlike SMBIOS it needs no firmware, so arm64's virt board has it too.
cat > kernel/configs/guest.config <<'EOF'
CONFIG_VIRTIO_MMIO=y
CONFIG_VIRTIO_MMIO_CMDLINE_DEVICES=y
CONFIG_VIRTIO_CONSOLE=y
CONFIG_VIRTIO_BALLOON=y
CONFIG_HW_RANDOM=y
CONFIG_HW_RANDOM_VIRTIO=y
CONFIG_VSOCKETS=y
CONFIG_VIRTIO_VSOCKETS=y
CONFIG_FUSE_FS=y
CONFIG_VIRTIO_FS=y
CONFIG_VIRT_DRIVERS=y
CONFIG_VMGENID=y
CONFIG_SERIAL_8250=y
CONFIG_SERIAL_8250_CONSOLE=y
CONFIG_FW_CFG_SYSFS=y
EOF
# Per-arch lines, since the check requires every =y here. arm64: qemu virt's PL011, PL031,
# PL061 + gpio-keys (the power button), and the ECAM host bridge.
case "$(uname -m)" in
    x86_64)  printf '%s\n' CONFIG_PVH=y CONFIG_SERIO_I8042=y CONFIG_KEYBOARD_ATKBD=y ;;
    aarch64) printf '%s\n' CONFIG_SERIAL_OF_PLATFORM=y CONFIG_SERIAL_AMBA_PL011=y \
                 CONFIG_SERIAL_AMBA_PL011_CONSOLE=y CONFIG_PCI_HOST_GENERIC=y \
                 CONFIG_RTC_DRV_PL031=y CONFIG_GPIO_PL061=y CONFIG_KEYBOARD_GPIO=y ;;
esac >> kernel/configs/guest.config
make guest.config

# The root: EROFS with zstd (what image/build-rootfs.sh's mkfs.erofs uses), on dm-verity.
# DM_INIT builds the verity device from `dm-mod.create=` with no initrd. MD is only the
# menu device-mapper lives in.
cat > kernel/configs/rootfs.config <<'EOF'
CONFIG_EROFS_FS=y
CONFIG_EROFS_FS_ZIP=y
CONFIG_EROFS_FS_ZIP_ZSTD=y
CONFIG_MD=y
CONFIG_BLK_DEV_DM=y
CONFIG_DM_INIT=y
CONFIG_DM_VERITY=y
EOF
make rootfs.config

# Hardening, from the Kernel Self Protection Project's recommendations as Talos and
# LinuxKit follow them. In the config rather than on the command line, which the host
# supplies and can change. Not named hardening.config: that is upstream's own fragment,
# in kernel/configs/ and again per arch, and `make hardening.config` merges all of them.
#   SECURITY_YAMA: ptrace limited to a process's descendants (kernel.yama.ptrace_scope,
#     image/files/etc/sysctl.d/50-hardening.conf). CONFIG_LSM already lists it.
#   INIT_ON_ALLOC_DEFAULT_ON: zero every allocation, so stale kernel memory never leaks
#     into a new one (init_on_alloc=1).
#   SLAB_MERGE_DEFAULT off: a cache of its own per object type, so an overflow in one
#     cannot reach another type's objects (slab_nomerge).
#   LEGACY_TIOCSTI off: only CAP_SYS_ADMIN may push input into a terminal, so a process
#     left on a terminal it shares (su, runuser, chroot --userspec) cannot type commands
#     into the shell that started it (CVE-2016-2781 and its kind; dev.tty.legacy_tiocsti).
cat > kernel/configs/kspp.config <<'EOF'
CONFIG_SECURITY_YAMA=y
CONFIG_INIT_ON_ALLOC_DEFAULT_ON=y
# CONFIG_SLAB_MERGE_DEFAULT is not set
# CONFIG_LEGACY_TIOCSTI is not set
EOF
make kspp.config

# What the debug extension's tracers attach to (docs/guides/debugging.md); an extension can add
# programs, never kernel features. Kprobes and uprobes, and the syscall tracepoints
# `perf trace` is built on. Not FUNCTION_TRACER (fentry/fexit): it costs every machine
# size, and nothing uses it yet.
cat > kernel/configs/trace.config <<'EOF'
CONFIG_KPROBES=y
CONFIG_KPROBE_EVENTS=y
CONFIG_UPROBE_EVENTS=y
CONFIG_FTRACE_SYSCALLS=y
EOF
make trace.config

# The other direction: hardware defconfig turns on that no VM has. Merged last, and it may
# not take back anything the fragments above turn on. A symbol that does not exist on this
# arch is fine; one that exists and stays on fails the check. When a line does not stick,
# clear the symbol that selects it.
cat > kernel/configs/vm.config <<'EOF'
# arm64's SoC platforms, each dragging in its pinctrl, clocks, PHYs, MMC, SPI and RTC.
# qemu virt needs none: it is device tree plus generic drivers (GIC, PL011, PSCI, virtio).
# CONFIG_ARCH_ACTIONS is not set
# CONFIG_ARCH_AIROHA is not set
# CONFIG_ARCH_ALPINE is not set
# CONFIG_ARCH_APPLE is not set
# CONFIG_ARCH_ARTPEC is not set
# CONFIG_ARCH_AXIADO is not set
# CONFIG_ARCH_BCM2835 is not set
# CONFIG_ARCH_BCMBCA is not set
# CONFIG_ARCH_BCM_IPROC is not set
# CONFIG_ARCH_BERLIN is not set
# CONFIG_ARCH_BLAIZE is not set
# CONFIG_ARCH_BRCMSTB is not set
# CONFIG_ARCH_BST is not set
# CONFIG_ARCH_CIX is not set
# CONFIG_ARCH_EXYNOS is not set
# CONFIG_ARCH_HISI is not set
# CONFIG_ARCH_INTEL_SOCFPGA is not set
# CONFIG_ARCH_K3 is not set
# CONFIG_ARCH_KEEMBAY is not set
# CONFIG_ARCH_LAYERSCAPE is not set
# CONFIG_ARCH_LG1K is not set
# CONFIG_ARCH_MA35 is not set
# CONFIG_ARCH_MEDIATEK is not set
# CONFIG_ARCH_MESON is not set
# CONFIG_ARCH_MVEBU is not set
# CONFIG_ARCH_MXC is not set
# CONFIG_ARCH_NPCM is not set
# CONFIG_ARCH_QCOM is not set
# CONFIG_ARCH_REALTEK is not set
# CONFIG_ARCH_RENESAS is not set
# CONFIG_ARCH_ROCKCHIP is not set
# CONFIG_ARCH_S32 is not set
# CONFIG_ARCH_SEATTLE is not set
# CONFIG_ARCH_SOPHGO is not set
# CONFIG_ARCH_SPARX5 is not set
# CONFIG_ARCH_SPRD is not set
# CONFIG_ARCH_STM32 is not set
# CONFIG_ARCH_SUNXI is not set
# CONFIG_ARCH_SYNQUACER is not set
# CONFIG_ARCH_TEGRA is not set
# CONFIG_ARCH_TESLA_FSD is not set
# CONFIG_ARCH_THUNDER is not set
# CONFIG_ARCH_THUNDER2 is not set
# CONFIG_ARCH_UNIPHIER is not set
# CONFIG_ARCH_VEXPRESS is not set
# CONFIG_ARCH_VISCONTI is not set
# CONFIG_ARCH_XGENE is not set
# CONFIG_ARCH_ZYNQMP is not set

# Buses the virt board does not have (x86_64_defconfig already leaves them off). Never add
# SERIAL_8250 here: it is amd64's and Firecracker's console.
# CONFIG_MTD is not set
# CONFIG_MMC is not set
# CONFIG_SPI is not set
# CONFIG_REGULATOR is not set
# CONFIG_BT is not set
# CONFIG_CAN is not set

# No radio (cfg80211 would ask for a regulatory.db at every boot). WLAN selects WIRELESS.
# CONFIG_WLAN is not set
# CONFIG_WIRELESS is not set
# CONFIG_RFKILL is not set

# No display: every console is serial or hvc0.
# CONFIG_DRM is not set
# CONFIG_AGP is not set
# CONFIG_SOUND is not set

# No USB, no HID. Input stays for virtio-input.
# CONFIG_USB_SUPPORT is not set
# CONFIG_HID_SUPPORT is not set
# CONFIG_INPUT_JOYSTICK is not set
# CONFIG_INPUT_TABLET is not set
# CONFIG_INPUT_TOUCHSCREEN is not set
# CONFIG_INPUT_MISC is not set

# Disks are virtio-blk: no SATA/PATA, CD-ROM, SCSI generic, RAID or PCMCIA. Loop and
# device-mapper (for verity) stay; the other dm targets go.
# CONFIG_DM_MIRROR is not set
# CONFIG_DM_ZERO is not set
# CONFIG_ATA is not set
# CONFIG_BLK_DEV_SR is not set
# CONFIG_CHR_DEV_SG is not set
# CONFIG_BLK_DEV_MD is not set
# CONFIG_PCCARD is not set
# CONFIG_MACINTOSH_DRIVERS is not set
# CONFIG_EEEPC_LAPTOP is not set
# No media devices; MEDIA_SUPPORT selects I2C on arm64.
# CONFIG_MEDIA_SUPPORT is not set
# CONFIG_I2C is not set
# CONFIG_WATCHDOG is not set
# CONFIG_NVRAM is not set
# CONFIG_DMADEVICES is not set

# The NIC is virtio-net, which lives outside the ETHERNET menu of vendor drivers.
# CONFIG_ETHERNET is not set
# CONFIG_NETCONSOLE is not set

# IOMMUs are for passing devices through, which is the host's side.
# CONFIG_AMD_IOMMU is not set
# CONFIG_INTEL_IOMMU is not set

# Power management a VM does not do. SCHED_MC_PRIO selects CPU_FREQ on x86.
# CONFIG_HIBERNATION is not set
# CONFIG_SCHED_MC_PRIO is not set
# CONFIG_CPU_FREQ is not set
# CONFIG_ACPI_DOCK is not set
# CONFIG_ACPI_BGRT is not set

# Filesystems with nothing to mount (no ESP: the kernel is passed directly). VFAT_FS
# selects FAT_FS. autofs stays for systemd's .automount units.
# CONFIG_ISO9660_FS is not set
# CONFIG_VFAT_FS is not set
# CONFIG_MSDOS_FS is not set
# CONFIG_FAT_FS is not set
# CONFIG_NFS_FS is not set
# CONFIG_QUOTA is not set
# CONFIG_NLS_CODEPAGE_437 is not set
# CONFIG_NLS_ISO8859_1 is not set

# No SELinux userspace or policy, and the packet labelling that exists for it. SECURITY
# stays for BPF_LSM.
# CONFIG_SECURITY_SELINUX is not set
# CONFIG_NETLABEL is not set
# CONFIG_NETFILTER_XT_TARGET_SECMARK is not set
# CONFIG_NETFILTER_XT_TARGET_CONNSECMARK is not set

# No auditd, and systemd is built -Daudit=disabled.
# CONFIG_AUDIT is not set

# No modules: modules_install never runs, so an =m would be silently missing. With this
# off every tristate resolves to y or n.
# CONFIG_MODULES is not set

# defconfig's debugging. DEBUG_KERNEL stays (kvm_guest.config, DEBUG_INFO_BTF).
# CONFIG_DEBUG_ENTRY is not set
# CONFIG_DEBUG_WX is not set
# CONFIG_DEBUG_STACK_USAGE is not set
# CONFIG_DEBUG_DEVRES is not set
# CONFIG_DEBUG_BOOT_PARAMS is not set
# CONFIG_PM_DEBUG is not set
# CONFIG_CGROUP_DEBUG is not set
# CONFIG_SCHEDSTATS is not set
# CONFIG_BLK_DEV_IO_TRACE is not set
# CONFIG_PROC_KCORE is not set
# CONFIG_PROFILING is not set
# CONFIG_KEXEC is not set
# CONFIG_PROVIDE_OHCI1394_DMA_INIT is not set
# CONFIG_EARLY_PRINTK_DBGP is not set
# CONFIG_X86_CHECK_BIOS_CORRUPTION is not set

# A cgroup controller for hardware with no driver here.
# CONFIG_CGROUP_RDMA is not set

# Being a hypervisor (KVM, VFIO), or a guest of one we do not target (Xen).
# CONFIG_XEN is not set
# CONFIG_KVM is not set
# CONFIG_VFIO is not set

# Firmware and management interfaces of real machines.
# CONFIG_CHROME_PLATFORMS is not set
# CONFIG_GOOGLE_FIRMWARE is not set
# CONFIG_TEE is not set
# CONFIG_TCG_TPM is not set
# CONFIG_IPMI_HANDLER is not set
# CONFIG_EDAC is not set
# CONFIG_ACPI_APEI_EINJ is not set
# CONFIG_ARM_SCMI_PROTOCOL is not set
# CONFIG_ARM_SCPI_PROTOCOL is not set
# CONFIG_MAILBOX is not set
# CONFIG_EFI_CAPSULE_LOADER is not set
# CONFIG_REMOTEPROC is not set
# CONFIG_RPMSG is not set
# CONFIG_RPMSG_VIRTIO is not set

# SoC buses and board-only device classes. ARM64 selects POWER_SUPPLY and POWER_RESET, so
# their drivers go instead; THERMAL stays for ACPI_PROCESSOR.
# CONFIG_SLIMBUS is not set
# CONFIG_SPMI is not set
# CONFIG_GREYBUS is not set
# CONFIG_MHI_BUS is not set
# CONFIG_IIO is not set
# CONFIG_PWM is not set
# CONFIG_FPGA is not set
# CONFIG_NEW_LEDS is not set
# CONFIG_BATTERY_BQ27XXX is not set
# CONFIG_POWER_RESET_GPIO is not set
# CONFIG_POWER_RESET_GPIO_RESTART is not set
# CONFIG_POWER_RESET_SYSCON is not set
# CONFIG_POWER_RESET_SYSCON_POWEROFF is not set
# CONFIG_POWER_RESET_XGENE is not set
# CONFIG_HWMON is not set
# CONFIG_INTERCONNECT is not set
# CONFIG_MEMORY is not set
# CONFIG_MULTIPLEXER is not set
# CONFIG_MDIO_BUS_MUX_MULTIPLEXER is not set
# CONFIG_EXTCON is not set
# CONFIG_COUNTER is not set
# CONFIG_HWSPINLOCK is not set
# CONFIG_HTE is not set
# CONFIG_PM_DEVFREQ is not set
# CONFIG_RC_CORE is not set
# CONFIG_MEDIA_CEC_SUPPORT is not set
# CONFIG_CORESIGHT is not set
# CONFIG_STM is not set
# CONFIG_STAGING is not set

# Other radios.
# CONFIG_NFC is not set
# CONFIG_WWAN is not set
# CONFIG_GNSS is not set

# PCI endpoint and SR-IOV (host side), SoC PCI hosts and SMMUs. The generic ECAM host
# stays (guest.config).
# CONFIG_PCI_ENDPOINT is not set
# CONFIG_PCI_IOV is not set
# CONFIG_PCIE_ALTERA is not set
# CONFIG_PCIE_KIRIN is not set
# CONFIG_PCIE_XILINX is not set
# CONFIG_PCI_MESON is not set
# CONFIG_PCI_XGENE is not set
# CONFIG_PCI_HOST_THUNDER_ECAM is not set
# CONFIG_PCI_HOST_THUNDER_PEM is not set
# CONFIG_ARM_SMMU is not set
# CONFIG_ARM_SMMU_V3 is not set

# Storage controllers no target VMM emulates. virtio-scsi stays.
# CONFIG_BLK_DEV_NVME is not set
# CONFIG_MEGARAID_SAS is not set
# CONFIG_SCSI_MPT3SAS is not set
# CONFIG_SCSI_UFSHCD is not set

# More filesystems with nothing to mount; host directories are virtiofs, not 9p.
# CONFIG_NET_9P is not set
# CONFIG_9P_FS is not set
# CONFIG_BTRFS_FS is not set
# CONFIG_SQUASHFS is not set
# CONFIG_CUSE is not set

# Switch silicon, TSN queueing, PHYs (and what selects them), and IPVS (kube-proxy runs
# nftables).
# CONFIG_NET_DSA is not set
# CONFIG_HSR is not set
# CONFIG_NET_SCH_CBS is not set
# CONFIG_NET_SCH_ETF is not set
# CONFIG_NET_SCH_TAPRIO is not set
# CONFIG_NET_SCH_MQPRIO is not set
# CONFIG_PCS_XPCS is not set
# CONFIG_PHYLIB is not set
# CONFIG_PHY_CADENCE_TORRENT is not set
# CONFIG_PHY_CADENCE_DPHY is not set
# CONFIG_PHY_CADENCE_DPHY_RX is not set
# CONFIG_PHY_CADENCE_SIERRA is not set
# CONFIG_PHY_CADENCE_SALVO is not set
# CONFIG_PHY_CAN_TRANSCEIVER is not set
# CONFIG_IP_VS is not set

# No framebuffer. VT stays: it is only settable under EXPERT, which changes defaults
# tree-wide.
# CONFIG_FB is not set
# CONFIG_LOGO is not set

# Desk input devices. i8042 and the GPIO power key stay (guest.config).
# CONFIG_INPUT_MOUSE is not set
# CONFIG_SERIO_AMBAKMI is not set
# CONFIG_KEYBOARD_GPIO_POLLED is not set

# UART variants and serial cards. SERIAL_8250 itself must stay.
# CONFIG_SERIAL_8250_EXTENDED is not set
# CONFIG_SERIAL_8250_PCI is not set
# CONFIG_SERIAL_8250_EXAR is not set
# CONFIG_SERIAL_8250_LPSS is not set
# CONFIG_SERIAL_8250_MID is not set
# CONFIG_SERIAL_8250_PERICOM is not set
# CONFIG_SERIAL_8250_DW is not set
# CONFIG_SERIAL_FSL_LPUART is not set
# CONFIG_SERIAL_FSL_LINFLEXUART is not set
# CONFIG_SERIAL_XILINX_PS_UART is not set
# CONFIG_SERIAL_DEV_BUS is not set

# CPU and SoC entropy drivers; the guest has virtio-rng and (arm64) the SMCCC TRNG.
# CONFIG_HW_RANDOM_INTEL is not set
# CONFIG_HW_RANDOM_AMD is not set
# CONFIG_HW_RANDOM_VIA is not set
# CONFIG_HW_RANDOM_ATMEL is not set
# CONFIG_HW_RANDOM_CN10K is not set
# CONFIG_HW_RANDOM_HISI_TRNG is not set

# Host PMUs. A guest only gets the architected core PMU.
# CONFIG_PERF_EVENTS_INTEL_UNCORE is not set
# CONFIG_PERF_EVENTS_INTEL_RAPL is not set
# CONFIG_PERF_EVENTS_INTEL_CSTATE is not set
# CONFIG_PERF_EVENTS_AMD_UNCORE is not set
# CONFIG_ARM_CCI_PMU is not set
# CONFIG_ARM_CCN is not set
# CONFIG_ARM_CMN is not set
# CONFIG_ARM_DMC620_PMU is not set
# CONFIG_ARM_DSU_PMU is not set
# CONFIG_ARM_SPE_PMU is not set
# CONFIG_ARM_SMMU_V3_PMU is not set
# CONFIG_ARM_CORESIGHT_PMU_ARCH_SYSTEM_PMU is not set
# CONFIG_HISI_PMU is not set

# A laptop's ACPI devices.
# CONFIG_ACPI_VIDEO is not set
# CONFIG_ACPI_AC is not set
# CONFIG_ACPI_BATTERY is not set
# CONFIG_ACPI_FAN is not set
# CONFIG_ACPI_THERMAL is not set

# arm64 board support outside the ARCH_* gates (drivers with a COMPILE_TEST or OF
# alternative).
# CONFIG_ASPEED_LPC_CTRL is not set
# CONFIG_ASPEED_LPC_SNOOP is not set
# CONFIG_ASPEED_P2A_CTRL is not set
# CONFIG_ASPEED_SOCINFO is not set
# CONFIG_ASPEED_UART_ROUTING is not set
# CONFIG_COMMON_CLK_ASPEED is not set
# CONFIG_COMMON_CLK_RP1 is not set
# CONFIG_COMMON_CLK_XGENE is not set
# CONFIG_COMMON_CLK_XLNX_CLKWZRD is not set
# CONFIG_MCHP_CLK_MPFS is not set
# CONFIG_VEXPRESS_CONFIG is not set
# CONFIG_ARM_TIMER_SP804 is not set
# CONFIG_MISC_RP1 is not set
# CONFIG_PINCTRL_RP1 is not set
# CONFIG_PINCTRL_SINGLE is not set
# CONFIG_POLARFIRE_SOC_IRQ_MUX is not set
# CONFIG_POLARFIRE_SOC_SYSCONS is not set
# CONFIG_RESET_POLARFIRE_SOC is not set
# CONFIG_RESET_TI_SYSCON is not set
# CONFIG_RESET_GPIO is not set
# CONFIG_SOC_TI is not set
# CONFIG_FSL_RCPM is not set
# CONFIG_SOCIONEXT_SYNQUACER_PREITS is not set
# CONFIG_XILINX_INTC is not set
# CONFIG_XILINX_SDFEC is not set
# CONFIG_XILINX_VCU is not set
# CONFIG_MFD_HI6421_PMIC is not set
# CONFIG_MFD_MT6397 is not set
# CONFIG_RTC_DRV_ZYNQMP is not set
# CONFIG_GPIO_ALTERA is not set
# CONFIG_GPIO_CADENCE is not set
# CONFIG_GPIO_DWAPB is not set
# CONFIG_GPIO_MB86S7X is not set
# CONFIG_GPIO_SYSCON is not set
# CONFIG_GPIO_XGENE is not set
# CONFIG_GPIO_XILINX is not set
# CONFIG_GPIO_AGGREGATOR is not set
# CONFIG_BACKLIGHT_CLASS_DEVICE is not set
# CONFIG_PCI_PWRCTRL_GENERIC is not set
# CONFIG_POWER_SEQUENCING is not set
# CONFIG_QRTR is not set
# CONFIG_UACCE is not set
# CONFIG_PCI_ENDPOINT_TEST is not set
# CONFIG_NVMEM_RMEM is not set
# CONFIG_NVMEM_LAYOUT_SL28_VPD is not set
# CONFIG_NVMEM_REBOOT_MODE is not set
# CONFIG_SYSCON_REBOOT_MODE is not set
# CONFIG_IOMMU_IO_PGTABLE_DART is not set
# CONFIG_IOMMU_IO_PGTABLE_ARMV7S is not set
# CONFIG_SCSI_SAS_LIBSAS is not set

# x86's equivalent: ME, WMI, laptop platforms, thermal MSRs, ACPI debugging.
# CONFIG_INTEL_MEI is not set
# CONFIG_X86_PLATFORM_DEVICES is not set
# CONFIG_ACPI_WMI is not set
# CONFIG_SURFACE_PLATFORMS is not set
# CONFIG_X86_PKG_TEMP_THERMAL is not set
# CONFIG_INTEL_TCC_COOLING is not set
# CONFIG_ACPI_DEBUG is not set
# CONFIG_ACPI_TABLE_UPGRADE is not set
# CONFIG_PNP_DEBUG_MESSAGES is not set

# Block and switch hardware features no virtual device has, and macvtap (for a host's
# guests). MACVLAN and 802.1Q stay: container networking.
# CONFIG_BLK_INLINE_ENCRYPTION is not set
# CONFIG_BLK_DEV_INTEGRITY is not set
# CONFIG_FW_UPLOAD is not set
# CONFIG_VLAN_8021Q_GVRP is not set
# CONFIG_VLAN_8021Q_MVRP is not set
# CONFIG_NET_SWITCHDEV is not set
# CONFIG_NET_ACT_GATE is not set
# CONFIG_MACVTAP is not set

# Features nothing in the image uses. The config ships as /boot/config, not config.gz.
# CONFIG_KEXEC_FILE is not set
# CONFIG_CRASH_DUMP is not set
# CONFIG_KSM is not set
# CONFIG_CMA is not set
# CONFIG_DMABUF_HEAPS is not set
# CONFIG_IKCONFIG is not set
# CONFIG_PSTORE_RAM is not set
# CONFIG_EFI_VARS_PSTORE is not set
# CONFIG_MEMTEST is not set
# CONFIG_LEGACY_PTYS is not set
# Firmware hardware-error reporting and RAS (arm64's defconfig): no hardware to report
# on. Then the rest of the boards' leftovers on arm64 — Qualcomm firmware, every crypto
# engine (Amlogic, HiSilicon, CryptoCell: CRYPTO_HW is their menu), aux displays, keypads, SRAM, GPU tracing — and parallel SCSI, serial
# mice and odd serial cards on amd64.
# CONFIG_ACPI_APEI is not set
# CONFIG_ACPI_HMAT is not set
# CONFIG_PCIEAER is not set
# CONFIG_MEMORY_FAILURE is not set
# CONFIG_RAS is not set
# CONFIG_QCOM_SCM is not set
# CONFIG_QCOM_PAS is not set
# CONFIG_CRYPTO_HW is not set
# CONFIG_AUXDISPLAY is not set
# CONFIG_INPUT_MATRIXKMAP is not set
# CONFIG_SRAM is not set
# CONFIG_TRACE_GPU_MEM is not set
# CONFIG_THERMAL_EMULATION is not set
# CONFIG_THERMAL_NETLINK is not set
# CONFIG_RAID_ATTRS is not set
# CONFIG_SCSI_SAS_ATTRS is not set
# CONFIG_SCSI_SPI_ATTRS is not set
# CONFIG_SCSI_CONSTANTS is not set
# CONFIG_SCSI_PROC_FS is not set
# CONFIG_SERIAL_8250_16550A_VARIANTS is not set
# CONFIG_SERIAL_NONSTANDARD is not set
# CONFIG_SERIO_SERPORT is not set
EOF
make vm.config

# What defconfig gives one architecture and not the other, decided once for both: a
# feature userspace can see is the same on amd64 and arm64. Hardware and the arch's own
# internals (ACPI, IOMMU defaults, clocks, pinctrl) still follow the arch; arm64's
# leftover boards are vm.config's. test/kernel-parity.sh holds the two configs to it.
#
# Merged after vm.config, last: a clear only takes if nothing selects the symbol at the
# time, and before vm.config amd64's BLK_DEV_IO_TRACE and SELinux still select RELAY and
# NETWORK_SECMARK, which vm.config's clears then leave behind on their own.
cat > kernel/configs/parity.config <<'EOF'
# ss reads sockets through sock_diag; without it, it falls back to parsing /proc/net.
CONFIG_INET_DIAG=y
CONFIG_INET_UDP_DIAG=y

# Policy routing (`ip rule`) and ECMP routes: CNIs (Cilium, Calico), WireGuard setups.
CONFIG_IP_ADVANCED_ROUTER=y
CONFIG_IP_MULTIPLE_TABLES=y
CONFIG_IP_ROUTE_MULTIPATH=y
# No multicast routing.
# CONFIG_IP_MROUTE is not set

# macvlan and VLAN networks (podman, the CNI plugins), VLAN-aware bridges.
CONFIG_MACVLAN=y
CONFIG_VLAN_8021Q=y
CONFIG_BRIDGE_VLAN_FILTERING=y

# tc: the ingress qdisc, flower and basic classifiers, drop and redirect actions — what
# CNIs and `tc` users build traffic steering from.
CONFIG_NET_SCH_INGRESS=y
CONFIG_NET_CLS_BASIC=y
CONFIG_NET_CLS_FLOWER=y
CONFIG_NET_ACT_GACT=y
CONFIG_NET_ACT_MIRRED=y
# tc's extended matches, and the cgroup v1 net_cls/net_prio controllers: cgroup v2 only.
# CONFIG_NET_EMATCH is not set
# CONFIG_NET_CLS_CGROUP is not set
# CONFIG_CGROUP_NET_CLASSID is not set
# CONFIG_CGROUP_NET_PRIO is not set

# Conntrack events (`conntrack -E`, CNIs watching flows).
CONFIG_NF_CONNTRACK_EVENTS=y
# No connection-tracking helpers: they parse FTP, IRC and SIP payloads in the kernel, for
# protocols nothing here speaks. No SECMARK: there is no SELinux.
# CONFIG_NF_CONNTRACK_FTP is not set
# CONFIG_NF_CONNTRACK_IRC is not set
# CONFIG_NF_CONNTRACK_SIP is not set
# CONFIG_NETWORK_SECMARK is not set
# The iptables vocabulary rules are written in: `-m state`, MSS clamping for overlay
# MTUs, NFLOG.
CONFIG_NETFILTER_XT_MATCH_STATE=y
CONFIG_NETFILTER_XT_TARGET_TCPMSS=y
CONFIG_NETFILTER_NETLINK_LOG=y
CONFIG_NETFILTER_XT_TARGET_NFLOG=y

# IPsec ESP and its policy match: the encrypted modes of Cilium, Calico and flannel,
# strongSwan. Not AH, which authenticates without encrypting and nothing uses.
CONFIG_XFRM_USER=y
CONFIG_INET_ESP=y
CONFIG_INET6_ESP=y
CONFIG_NETFILTER_XT_MATCH_POLICY=y
# CONFIG_INET6_AH is not set

# TCP: SYN cookies under a SYN flood, MD5 signatures (BGP: Calico, MetalLB), and a choice
# of congestion control (cubic stays the default).
CONFIG_SYN_COOKIES=y
CONFIG_TCP_MD5SIG=y
CONFIG_TCP_CONG_ADVANCED=y

# cgroup v2's io.cost, io.latency and io.prio (systemd's IOWeight=,
# IODeviceLatencyTargetSec=, IOSchedulingClass=). No BFQ: virtio queues run without a
# scheduler.
CONFIG_BLK_CGROUP_IOCOST=y
CONFIG_BLK_CGROUP_IOLATENCY=y
CONFIG_BLK_CGROUP_IOPRIO=y
# CONFIG_IOSCHED_BFQ is not set

# Huge pages for whoever asks with madvise (databases want them, and most warn about
# always), fanotify, per-task delay accounting (iotop), the LSM network hooks a BPF LSM
# program attaches to.
CONFIG_TRANSPARENT_HUGEPAGE=y
CONFIG_TRANSPARENT_HUGEPAGE_MADVISE=y
CONFIG_FANOTIFY=y
CONFIG_TASK_DELAY_ACCT=y
CONFIG_SECURITY_NETWORK=y
CONFIG_CORE_DUMP_DEFAULT_ELF_HEADERS=y
# Desktop and big-iron behaviour a VM has no use for: autogrouping by session, NUMA
# balancing, the proc connector, configfs, NBD, pstore, crypto through netlink.
# CONFIG_SCHED_AUTOGROUP is not set
# CONFIG_NUMA_BALANCING is not set
# CONFIG_CONNECTOR is not set
# CONFIG_CONFIGFS_FS is not set
# CONFIG_BLK_DEV_NBD is not set
# CONFIG_PSTORE is not set
# CONFIG_CRYPTO_USER is not set
# CONFIG_CRYPTO_USER_API_RNG is not set
# CONFIG_RELAY is not set
# CONFIG_FANOTIFY_ACCESS_PERMISSIONS is not set
# CONFIG_FW_LOADER_USER_HELPER is not set
# CONFIG_BSD_PROCESS_ACCT_V3 is not set
# CONFIG_CGROUP_MISC is not set

# Up to 512 vCPUs (amd64's default stops at 64, and the rest of a bigger VM's would sit
# idle), one size of kernel log, one list of security modules in one order.
CONFIG_NR_CPUS=512
CONFIG_LOG_BUF_SHIFT=18
CONFIG_LSM="landlock,lockdown,yama,loadpin,safesetid,ipe,bpf"

# One timer frequency and one preemption model: amd64's defaults, lazy preemption with
# `preempt=` on the command line to change it.
CONFIG_HZ_1000=y
CONFIG_PREEMPT_LAZY=y
CONFIG_PREEMPT_DYNAMIC=y

# ip6tables' -m ipv6header, verbose route logging, LOG for ARP and IPv4 (amd64's).
CONFIG_IP6_NF_MATCH_IPV6HEADER=y
CONFIG_IP_ROUTE_VERBOSE=y
CONFIG_NF_LOG_ARP=y
CONFIG_NF_LOG_IPV4=y
# No kernel RARP autoconfiguration (`ip=` stays), no codepages without a filesystem
# that uses them, and no congestion control but cubic that nobody chose.
# CONFIG_IP_PNP_RARP is not set
# CONFIG_NLS_ASCII is not set
# CONFIG_NLS_UTF8 is not set
# CONFIG_TCP_CONG_BIC is not set
# CONFIG_TCP_CONG_HTCP is not set
# CONFIG_TCP_CONG_WESTWOOD is not set
# Generic crypto arm64's defconfig adds for its boards' drivers: legacy and national
# ciphers and hashes, key agreement, compression through the crypto API (EROFS selects
# DEFLATE only for hardware-accelerated decompression, which is off).
# CONFIG_CRYPTO_DES is not set
# CONFIG_CRYPTO_MD5 is not set
# CONFIG_CRYPTO_SM3 is not set
# CONFIG_CRYPTO_SM4_GENERIC is not set
# CONFIG_CRYPTO_SHA3 is not set
# CONFIG_CRYPTO_XTS is not set
# CONFIG_CRYPTO_CHACHA20 is not set
# CONFIG_CRYPTO_DH is not set
# CONFIG_CRYPTO_ECDH is not set
# CONFIG_CRYPTO_DEFLATE is not set
# CONFIG_CRYPTO_ZSTD is not set
EOF
make parity.config

# Check the fragments took, because olddefconfig drops what it cannot apply in silence:
#   - every symbol named is defined by some Kconfig, so a rename upstream fails here
#     instead of quietly bringing a driver back (a clear for a symbol the pinned kernel
#     does not have yet goes in with the bump);
#   - everything the additive fragments turn on is =y;
#   - nothing cleared came back =y.
fragments=(kernel/configs/{container,rootfs,k8s,parity,guest,trace,kspp}.config)
unapplied=""

kconfig_symbols=$(find . -name 'Kconfig*' -not -path './Documentation/*' -print0 \
    | xargs -0 sed -n -E 's/^[[:space:]]*(menu)?config[[:space:]]+([A-Z0-9_]+)[[:space:]]*$/\2/p' \
    | sort -u)
while read -r sym; do
    if ! grep -qx "$sym" <<< "$kconfig_symbols"; then
        unapplied="$unapplied  CONFIG_$sym  (no Kconfig in this kernel defines it — renamed or removed?)"$'\n'
    fi
done < <(sed -n -E 's/^# CONFIG_([A-Z0-9_]+) is not set$/\1/p; s/^CONFIG_([A-Z0-9_]+)=.*/\1/p' \
             "${fragments[@]}" kernel/configs/vm.config | sort -u)
while read -r sym; do
    if ! grep -qx "$sym" .config; then
        unapplied="$unapplied  $sym  (asked for, not in .config)"$'\n'
    fi
done < <(grep -hE '^CONFIG_[A-Z0-9_]+=y$' "${fragments[@]}")

while read -r sym; do
    if grep -qx "$sym=y" .config; then
        unapplied="$unapplied  $sym  (cleared, came back =y)"$'\n'
    fi
done < <(sed -n 's/^# \(CONFIG_[A-Z0-9_]*\) is not set$/\1/p' kernel/configs/container.config \
             kernel/configs/parity.config kernel/configs/kspp.config kernel/configs/vm.config)

if [ -n "$unapplied" ]; then
    echo "error: config fragments did not apply as written:" >&2
    printf '%s' "$unapplied" >&2
    exit 1
fi

make

# Staged as boot/bzImage on both arches, so nothing downstream needs to know which.
case "$(uname -m)" in
    x86_64)  kernel_image=arch/x86/boot/bzImage ;;
    aarch64) kernel_image=arch/arm64/boot/Image ;;
    *) echo "error: unsupported build architecture: $(uname -m) (expected x86_64 or aarch64)" >&2
       exit 1 ;;
esac

# Never loaded from the image (VMMs are passed the kernel), but it keeps the artifact
# bootable on its own.
install -D -m 644 "$kernel_image" $ROOTFS/boot/bzImage

# The resolved config, which test/kernel-caps.sh reads.
install -D -m 644 .config $ROOTFS/boot/config

# There are no modules, but /usr/lib/modules/<release> has to exist: kubeadm's preflight
# reads the config from there, and kube-proxy and CNI pods bind-mount /lib/modules, which
# a read-only root cannot create at runtime.
release=$(make -s kernelrelease)
install -d $ROOTFS/usr/lib/modules/$release
ln -sfn ../../../../boot/config $ROOTFS/usr/lib/modules/$release/config
