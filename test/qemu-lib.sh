#!/usr/bin/env bash
# The qemu plumbing shared by the boot tests and tools/boot-qemu.sh.
#
# Sourced, never executed, so that debugging a failed boot with tools/boot-qemu.sh boots
# the same machine CI did. The caller sets ROOTFS/KERNEL/INIT and whatever else it needs, then:
#
#   qemu_setup       normalize $ARCH; pick the binary, console device and accel flags
#   qemu_preflight   refuse now if the binary or the images are missing
#   qemu_ssh_setup   optional: an ephemeral key, a port forward to the guest's sshd and
#                    the credential that authorizes the one over the other (see below)
#   qemu_argv        build the common command line into $QEMU_ARGV
#   qemu_boot        run it in the background on a fifo, logging to $LOG
#
# ...and then drives the console with console_send / console_mark / await /
# console_login. tools/boot-qemu.sh stops after qemu_argv and execs it instead, handing
# the console to the terminal.
#
# kaetzli.qemu (python/) is the same harness in Python, which test/boot.py uses; the two
# are side by side until every boot test has moved (docs/proposals/host-scripting.md,
# item 7). A change here is a change there: python/tests/test_qemu_machine.py runs both
# and requires the same command line, and the same QEMU_DIED, CONSOLE_BAD and login.

source tools/lib.sh   # normalize_arch

# ROOTFS, KERNEL and INIT have no default: a wrong one boots the wrong thing quietly.
TIMEOUT="${TIMEOUT:-300}"   # generous: CI has no KVM
MEM="${MEM:-1024}"
CPUS="${CPUS:-2}"
LOGIN_USER="${LOGIN_USER:-root}"

# What a guest looks like on its way out. Matched by await; kaetzli.qemu.console has the same list.
QEMU_DIED='Kernel panic|Attempted to kill init|Requesting system (poweroff|reboot)'

# What must never appear on a console, whatever the test is about: a guest that logs one
# of these usually keeps running, so nothing else would fail. Matched over the whole log
# when a boot ends without the test having failed (cleanup, below). A test that provokes
# one on purpose sets CONSOLE_BAD_EXEMPT to an extended regex of the lines it expects.
# From Flatcar's kola, which runs the same kind of list over every test's console.
CONSOLE_BAD='Oops:|BUG: |WARNING: CPU: [0-9]+ PID: [0-9]+ at |general protection fault|'\
'Call Trace:|segfault at |=11/SEGV|[Cc]ore dump|EXT4-fs error|Remounting filesystem read-only|'\
'device-mapper: verity: .* is corrupted|You are in emergency mode|Starting Emergency Shell'

# ARCH defaults to the host's. QEMU_PROFILE picks the machine's shape, approximating the
# VMMs the kernel is built for by the devices they give a guest:
#
#   pc       qemu's own PCI machine, virtio-pci devices, a UART console. The default, and
#            what every other test boots.
#   microvm  Firecracker's shape: no PCI device at all, everything virtio over MMIO, a UART
#            console. On amd64 that is qemu's microvm machine with ACPI off, booting an
#            uncompressed vmlinux through PVH — which is how Firecracker boots one.
#   vz       Virtualization.framework's shape: a virtio console (hvc0) and no UART,
#            virtiofs when VIRTIOFS_SOCKET names a virtiofsd, vsock when VSOCK_CID is set.
#
# VAR_DISK and SYSEXTS are only wired into pc; the other two are about the devices, not
# the state.
qemu_setup() {
    ARCH=$(normalize_arch "${ARCH:-}")
    PROFILE="${QEMU_PROFILE:-pc}"
    case "$PROFILE" in
        pc|microvm|vz) ;;
        *) echo "error: QEMU_PROFILE=$PROFILE (expected pc, microvm or vz)" >&2; exit 1 ;;
    esac

    # arm64 has no default machine type; its console is the virt board's PL011.
    accel=()
    machine=()
    if [ "$ARCH" = arm64 ]; then
        QEMU=qemu-system-aarch64
        CONSOLE=ttyAMA0
        if [ -w /dev/kvm ]; then
            machine=(-machine virt,accel=kvm -cpu host)
        else
            echo "note: /dev/kvm not available, emulating (slow)"
            machine=(-machine virt -cpu max)
        fi
    else
        QEMU=qemu-system-x86_64
        CONSOLE=ttyS0
        # -cpu max, not qemu64: Ubuntu 26.04's gcc builds for x86-64-v3, which glibc
        # refuses to run on a CPU without AVX2.
        if [ -w /dev/kvm ]; then
            accel=(-enable-kvm -cpu host)
        else
            echo "note: /dev/kvm not available, emulating (slow)"
            accel=(-cpu max)
        fi
        # ACPI off, so devices are announced on the command line as Firecracker does.
        # pit=on: without KVM a guest with no PIT hangs at "Unable to calibrate against
        # PIT". arm64 keeps the virt board, which has MMIO transports already.
        [ "$PROFILE" != microvm ] ||
            machine=(-machine microvm,acpi=off,pit=on,rtc=on,isa-serial=on)
    fi
    [ "$PROFILE" != vz ] || CONSOLE=hvc0
}

# Where tools/fetch-image.sh puts the image (-$ARCH only for a foreign arch), for the
# interactive boot scripts.
fetched_image_defaults() {
    if [ "$ARCH" = "$(normalize_arch)" ]; then
        OUT="${OUT:-output/boot-image}"
    else
        OUT="${OUT:-output/boot-image-$ARCH}"
    fi
    KERNEL="${KERNEL:-$OUT/bzImage}"
    ROOTFS="${ROOTFS:-$OUT/rootfs.erofs}"
    INIT="${INIT:-/usr/lib/systemd/systemd}"
    QEMU_HINT="(run ./tools/fetch-image.sh)"
}

# $QEMU_HINT names the command that would produce a missing file.
qemu_preflight() {
    command -v "$QEMU" >/dev/null || { echo "error: $QEMU not found" >&2; exit 1; }
    [ -f "$KERNEL" ] || { echo "error: missing kernel: $KERNEL${QEMU_HINT:+ $QEMU_HINT}" >&2; exit 1; }
    [ -f "$ROOTFS" ] || { echo "error: missing rootfs: $ROOTFS${QEMU_HINT:+ $QEMU_HINT}" >&2; exit 1; }
    [ -f "$(root_cmdline_file)" ] || {
        echo "error: missing $(root_cmdline_file), the kernel arguments that go with $ROOTFS" >&2
        echo "       image/build-rootfs.sh writes it next to the image; it carries the dm-verity root hash" >&2
        exit 1
    }
}

# The root's kernel arguments, with its dm-verity root hash, written by
# image/build-rootfs.sh beside the image. ROOT_CMDLINE names another file.
root_cmdline_file() {
    echo "${ROOT_CMDLINE:-$(dirname "$ROOTFS")/rootfs.cmdline}"
}

# Passwordless ssh, the way Vagrant and Lima do it: a key pair for this boot only (deleted
# with $SSH_DIR when the guest goes), the public half passed as the systemd credential
# ssh.authorized_keys.root on the kernel command line (not SMBIOS, which arm64's virt board
# lacks without firmware), a port forward from 127.0.0.1:$SSH_PORT (the first free one
# from 2222) to the guest's 22, and an ssh_config for tools/ssh.sh.
#
# SSH_PROXY replaces the forward with a ProxyCommand, for VMMs with only vsock
# (tools/boot-firecracker.sh, boot-vz.sh); systemd's ssh generator has sshd listen there.
#
# boot-vz.sh sources this on macOS: stay within bash 3.2 and BSD tools here.
qemu_ssh_setup() {
    command -v ssh-keygen >/dev/null || { echo "error: ssh-keygen not found (install an openssh client)" >&2; exit 1; }
    SSH_DIR="${SSH_DIR:-output/ssh}"
    if [ -z "${SSH_PORT:-}" ] && [ -z "${SSH_PROXY:-}" ]; then
        SSH_PORT=2222
        while (exec 9<>"/dev/tcp/127.0.0.1/$SSH_PORT") 2>/dev/null; do
            SSH_PORT=$((SSH_PORT + 1))
        done
    fi

    # SSH_KEEP_KEY=1 keeps a key pair already in $SSH_DIR (tools/vm.sh, whose machines
    # outlive one boot); only the config, with this boot's port, is written again.
    if [ "${SSH_KEEP_KEY:-0}" = 1 ] && [ -f "$SSH_DIR/id_ed25519" ] && [ -f "$SSH_DIR/id_ed25519.pub" ]; then
        chmod 700 "$SSH_DIR"
    else
        rm -rf "$SSH_DIR"
        mkdir -p -m 700 "$SSH_DIR"
        ssh-keygen -q -t ed25519 -N '' -C "kaetzli-ephemeral@$(hostname)" -f "$SSH_DIR/id_ed25519"
    fi

    # No host key checking: the guest makes new host keys every boot, and this is a
    # loopback to a VM just started. IdentitiesOnly so an agent's keys do not exhaust
    # MaxAuthTries first; BatchMode so a refusal is an error, not a password prompt.
    local reach="    ProxyCommand ${SSH_PROXY:-}"
    [ -n "${SSH_PROXY:-}" ] || reach="    HostName 127.0.0.1
    Port $SSH_PORT"
    cat > "$SSH_DIR/config" <<EOF
Host kaetzli
$reach
    User root
    IdentityFile $(realpath "$SSH_DIR/id_ed25519")
    IdentitiesOnly yes
    BatchMode yes
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
EOF

    [ -n "${SSH_PROXY:-}" ] || QEMU_NIC_EXTRA=",hostfwd=tcp:127.0.0.1:$SSH_PORT-:22"
    QEMU_APPEND_EXTRA=" systemd.set_credential_binary=ssh.authorized_keys.root:$(base64 < "$SSH_DIR/id_ed25519.pub" | tr -d '\n')"
    QEMU_SSH_DIR="$SSH_DIR"
}

# The root is attached read-only too, so one file can back any number of guests.
# QEMU_NIC_ARGS replaces pc's user-mode NIC (tools/k8s-demo.sh's own segment).
qemu_argv() {
    local root_args devices=() display=(-nographic) nic fwcfg=() cred
    root_args=$(cat "$(root_cmdline_file)")
    qemu_var_disk
    qemu_sysexts
    # The extensions' verity tables go into the root's dm-mod.create= — the kernel takes
    # one, with devices separated by ';' — and their disks into what it waits for.
    if [ -n "$QEMU_DM_TABLES" ]; then
        root_args=$(printf '%s' "$root_args" | sed -e "s|\"\$|$QEMU_DM_TABLES\"|" \
            -e "s|dm-mod.waitfor=\([^ ]*\)|dm-mod.waitfor=\1$QEMU_DM_WAITFOR|")
    fi
    case "$PROFILE" in
        pc)
            nic=(-nic "user,model=virtio-net-pci${QEMU_NIC_EXTRA:-}")
            [ -z "${QEMU_NIC_ARGS:-}" ] || nic=("${QEMU_NIC_ARGS[@]}")
            # QEMU_FWCFG_CREDENTIALS: name=value pairs, separated by spaces, handed to
            # systemd through qemu's fw_cfg rather than on the command line (FW_CFG_SYSFS
            # in guest.config), as libvirt's <sysinfo type="fwcfg"> does. pc only, on
            # both arches: microvm and vz stand for VMMs that have no fw_cfg. A comma in a
            # value would have to be doubled for qemu.
            for cred in ${QEMU_FWCFG_CREDENTIALS:-}; do
                fwcfg+=(-fw_cfg "name=opt/io.systemd.credentials/${cred%%=*},string=${cred#*=}")
            done
            # The order names the devices: root /dev/vda, /var /dev/vdb, then extensions.
            # All explicit -device, since qemu adds if=virtio disks after those.
            devices=(
                -drive id=root,file="$ROOTFS",format=raw,if=none,readonly=on
                -device virtio-blk-pci,drive=root
                "${QEMU_VAR_DRIVE[@]}"
                "${QEMU_SYSEXT_DRIVES[@]}"
                "${nic[@]}"
                "${fwcfg[@]}"
            ) ;;
        microvm)
            # Everything on the MMIO transport, which is all Firecracker has.
            devices=(
                -drive id=root,file="$ROOTFS",format=raw,if=none,readonly=on
                -device virtio-blk-device,drive=root
                -netdev "user,id=net0${QEMU_NIC_EXTRA:-}" -device virtio-net-device,netdev=net0
                -device virtio-rng-device
            ) ;;
        vz)
            # Virtualization.framework's devices: hvc0 and no UART, virtiofs (which needs
            # shared memory with virtiofsd), vsock.
            display=(-display none -serial none -monitor none -vga none)
            devices=(
                -drive file="$ROOTFS",format=raw,if=virtio,readonly=on
                -nic "user,model=virtio-net-pci${QEMU_NIC_EXTRA:-}"
                -chardev stdio,id=con,signal=off -device virtio-serial-pci
                -device virtconsole,chardev=con
                -device virtio-rng-pci -device virtio-balloon-pci
                -object "memory-backend-memfd,id=mem,size=${MEM}M,share=on" -numa node,memdev=mem
            )
            [ -z "${VIRTIOFS_SOCKET:-}" ] || devices+=(
                -chardev "socket,id=vfs,path=$VIRTIOFS_SOCKET"
                -device vhost-user-fs-pci,chardev=vfs,tag=share
            )
            [ -z "${VSOCK_CID:-}" ] || devices+=(-device "vhost-vsock-pci,guest-cid=$VSOCK_CID")
            ;;
    esac
    # QEMU_DISPLAY_ARGS replaces pc's -nographic: tools/vm.sh puts the console on a socket.
    [ "$PROFILE" != pc ] || [ -z "${QEMU_DISPLAY_ARGS+set}" ] || display=("${QEMU_DISPLAY_ARGS[@]}")
    QEMU_ARGV=(
        "${accel[@]}" "${machine[@]}"
        -m "$MEM" -smp "$CPUS"
        -kernel "$KERNEL"
        "${devices[@]}"
        -append "$root_args console=$CONSOLE init=$INIT systemd.set_credential=login.noauth:yes${QEMU_APPEND_EXTRA:-}${QEMU_VAR_APPEND:-}${QEMU_SYSEXT_APPEND:-}"
        "${display[@]}"
    )
    # A guest's reboot ends qemu, which is what a test waiting on it wants. QEMU_REBOOT=1
    # restarts it in place instead, with the same command line — and so the same disks,
    # key and credentials (tools/vm.sh).
    [ "${QEMU_REBOOT:-0}" = 1 ] || QEMU_ARGV+=(-no-reboot)
}

# A /var disk seeded with <tree> (tools/k8s-demo.sh, test/sysext.sh). mkfs.ext4 -d records
# owners, and a /var/lib not owned by root makes tmpfiles refuse it ("unsafe path
# transition"), hence podman unshare.
qemu_seed_var_disk() { # disk size tree
    local disk="$1" size="$2" tree="$3" run=()
    command -v mkfs.ext4 >/dev/null || { echo "error: mkfs.ext4 not found (install e2fsprogs)" >&2; exit 1; }
    if [ "$(id -u)" != 0 ]; then
        command -v podman >/dev/null || { echo "error: podman not found, which seeds a /var disk owned by root" >&2; exit 1; }
        run=(podman unshare)
    fi
    rm -f "$disk"
    truncate -s "$size" "$disk"
    "${run[@]}" mkfs.ext4 -q -F -d "$tree" "$disk"
}

# SYSEXTS: each <name> is a read-only disk of $EXTENSIONS/{extensions,confexts}/<name>.raw
# (default: beside the image), verified like the root — its <name>.dm-verity table joins
# the root's dm-mod.create=, naming its disk by position — and mounted under
# /run/{extensions,confexts}/<name>, where systemd-sysext finds it (docs/guides/sysext.md).
qemu_sysexts() {
    QEMU_SYSEXT_DRIVES=() QEMU_SYSEXT_APPEND= QEMU_DM_TABLES= QEMU_DM_WAITFOR=
    [ -n "${SYSEXTS:-}" ] || return 0
    local name class dir file id table dev letters=bcdefghijklmnopqrstuvwxyz
    local ext_dir="${EXTENSIONS:-$(dirname "$ROOTFS")}" next=0
    [ -z "${VAR_DISK:-}" ] || next=1
    for name in $SYSEXTS; do
        [ -f "$ext_dir/extensions/$name.raw" ] || {
            echo "error: SYSEXTS names $name, but there is no $ext_dir/extensions/$name.raw" >&2
            echo "       (tools/build-image.sh builds it, tools/fetch-image.sh downloads CI's)" >&2
            exit 1
        }
        for class in ext conf; do
            dir=extensions; [ "$class" = ext ] || dir=confexts
            file="$ext_dir/$dir/$name.raw"
            [ -f "$file" ] || continue
            [ -f "${file%.raw}.dm-verity" ] || { echo "error: $file has no ${file%.raw}.dm-verity beside it" >&2; exit 1; }
            id="$class-$name"
            dev="/dev/vd${letters:$next:1}"
            next=$((next + 1))
            table=$(sed "s|@DEV@|$dev|g" "${file%.raw}.dm-verity")
            QEMU_SYSEXT_DRIVES+=(-drive "file=$file,format=raw,if=none,readonly=on,id=$id"
                                 -device "virtio-blk-pci,drive=$id")
            QEMU_DM_TABLES+=";$id,,,ro,$table"
            QEMU_DM_WAITFOR+=",$dev"
            QEMU_SYSEXT_APPEND+=" systemd.mount-extra=/dev/mapper/$id:/run/$dir/$name:erofs:ro"
        done
    done
}

# VAR_DISK: a persistent /var on a disk of its own (sparse, VAR_SIZE, default 4G). The
# guest formats it on first boot and grows it later; the generated var.mount outranks the
# image's tmpfs one. The machine ID lives beside it, since state and identity go together.
qemu_var_disk() {
    QEMU_VAR_DRIVE=() QEMU_VAR_APPEND=
    [ -n "${VAR_DISK:-}" ] || return 0
    if [ ! -e "$VAR_DISK" ]; then
        mkdir -p "$(dirname "$VAR_DISK")"
        # A sparse file either way; macOS has mkfile rather than truncate.
        if command -v truncate >/dev/null; then
            truncate -s "${VAR_SIZE:-4G}" "$VAR_DISK"
        else
            mkfile -n "$(echo "${VAR_SIZE:-4G}" | tr '[:upper:]' '[:lower:]')" "$VAR_DISK"
        fi
    fi
    if [ ! -s "$VAR_DISK.machine-id" ]; then
        od -An -tx1 -N16 /dev/urandom | tr -d ' \n' > "$VAR_DISK.machine-id"
    fi
    QEMU_VAR_DRIVE=(-drive "id=var,file=$VAR_DISK,format=raw,if=none" -device virtio-blk-pci,drive=var)
    QEMU_VAR_APPEND=" systemd.mount-extra=/dev/vdb:/var:ext4:x-systemd.makefs,x-systemd.growfs"
    QEMU_VAR_APPEND+=" systemd.machine_id=$(cat "$VAR_DISK.machine-id")"
}

# Launch it in the background with its console on a fifo we hold open, and its output in
# $LOG. Sets $qemu_pid and $deadline, installs the cleanup trap, and opens fd 3 as the way
# to type at the guest.
qemu_boot() {
    qemu_argv

    work=$(mktemp -d)
    console="$work/console-in"
    mkfifo "$console"
    mkdir -p "$(dirname "$LOG")"
    : > "$LOG"

    "$QEMU" "${QEMU_ARGV[@]}" < "$console" > "$LOG" 2>&1 &
    qemu_pid=$!

    # Also called by hand between boots (test/vmm.sh, test/sysext.sh). $? is the status
    # the script is exiting with, or the last command's on a call by hand (a trap that
    # runs something first passes it as $1): only a boot that has not already failed is
    # held to the console check, so a failure reports its own reason first.
    cleanup() {
        local rc=${1:-$?}
        { exec 3>&-; } 2>/dev/null || true
        kill "$qemu_pid" 2>/dev/null || true
        wait "$qemu_pid" 2>/dev/null || true
        rm -rf "$work"
        [ -z "${QEMU_SSH_DIR:-}" ] || rm -rf "$QEMU_SSH_DIR"
        [ "$rc" != 0 ] || console_check
    }
    trap cleanup EXIT

    # Read-write, so opening never blocks on qemu having got as far as its stdin.
    exec 3<> "$console"

    deadline=$((SECONDS + TIMEOUT))
    echo ">> booting $ROOTFS with $KERNEL (init=$INIT), waiting up to ${TIMEOUT}s"
}

console_send() { printf '%s\n' "$1" >&3; }

# How much console output there is so far, as a byte offset into the log — the caller takes
# one of these before it types something and passes it to await, so that only the guest's
# answer can satisfy the wait. See console_login below for why that matters.
console_mark() { wc -c < "$LOG"; }

# $TEST_NAME names the test in the failure line; $DIAGNOSE, when set, is typed at the guest
# first so its answer lands in the transcript below. If the guest never got as far as a
# shell that just scrolls past, and the console log is what there is to go on.
fail() {
    echo ">> ${TEST_NAME:-boot} test FAILED: $1" >&2
    [ -n "${DIAGNOSE:-}" ] && { console_send "$DIAGNOSE" 2>/dev/null || true; sleep 5; }
    echo ">> last 80 lines of the console:" >&2
    tail -80 "$LOG" >&2
    exit 1
}

# The $CONSOLE_BAD lines in $LOG, minus $CONSOLE_BAD_EXEMPT; exits 1 naming them. Not
# through fail: the guest is gone by now, so there is nothing to DIAGNOSE.
console_check() {
    local bad
    bad=$(tr -d '\r' < "$LOG" | grep -aE "$CONSOLE_BAD" |
          { if [ -n "${CONSOLE_BAD_EXEMPT:-}" ]; then grep -avE "$CONSOLE_BAD_EXEMPT"; else cat; fi; } || true)
    [ -n "$bad" ] || return 0
    echo ">> ${TEST_NAME:-boot} test FAILED: the console shows a kernel warning, a crash or an emergency shell (CONSOLE_BAD in test/qemu-lib.sh):" >&2
    printf '%s\n' "$bad" | head -10 | sed 's/^/   /' >&2
    echo ">> the whole console is in $LOG" >&2
    exit 1
}

# Wait for a pattern to show up on the console, keeping an eye on the guest being alive and
# on the overall deadline. Returns 1 on the local timeout so the caller can retry. With a
# third argument, output before that offset is ignored.
await() { # pattern seconds [since]
    local until=$((SECONDS + $2)) since="${3:-0}"
    while [ "$SECONDS" -lt "$until" ]; do
        tail -c "+$((since + 1))" "$LOG" | grep -qaE "$1" && return 0
        grep -qaE "$QEMU_DIED" "$LOG" && fail "the guest died"
        kill -0 "$qemu_pid" 2>/dev/null || fail "qemu exited"
        [ "$SECONDS" -lt "$deadline" ] || fail "timed out waiting for: $1"
        sleep 2
    done
    return 1
}

# The marker is split by quotes so the echo of what we typed cannot match it.
READY="SHELL-IS-UP"
PROBE="echo SHELL-IS'-UP'"
# Echo off, prompt and colours off: the log is read as text.
QUIET="stty -echo; PS1=; export SYSTEMD_COLORS=0"

# Log in at the serial getty and leave a quiet shell on fd 3. The username is the whole
# login: qemu_argv passes the login.noauth credential. Retried, since agetty loses what is
# typed while it sets the line up; only output after the username satisfies the wait.
console_login() {
    await 'login:' "$TIMEOUT" || fail "no login prompt"
    echo ">> got a login prompt, logging in as $LOGIN_USER"

    until grep -qaF "$READY" "$LOG"; do
        [ "$SECONDS" -lt "$deadline" ] || fail "could not get a shell (login rejected?)"
        local prompt
        prompt=$(console_mark)
        console_send "$LOGIN_USER"
        sleep 3
        console_send "$QUIET"
        console_send "$PROBE"
        await "$READY" 10 "$prompt" || true
    done
}
