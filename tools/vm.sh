#!/usr/bin/env bash
# Long-lived qemu machines of the image: each one keeps its /var on a disk of its own, so
# what is installed, pulled or configured there survives a reboot, a restart and an
# image update, while the root stays the read-only image it booted.
#
#     ./tools/vm.sh start                      # boot `kaetzli` in the background, wait for ssh
#     ./tools/vm.sh ssh [--root] [cmd...]      # log in (as `user` unless --root); scp: see below
#     ./tools/vm.sh console                    # its serial console (Ctrl-] leaves)
#     ./tools/vm.sh stop                       # power it off
#     ./tools/vm.sh run                        # or: in the foreground, console here (Ctrl-a x)
#
#     ./tools/vm.sh start dev --ext podman,debug --mem 4096 --size 20G
#     ./tools/vm.sh ssh dev podman ps          # the first argument is a VM when one has that name
#     ./tools/vm.sh list                       # every machine: state, ssh port, image, extensions
#     ./tools/vm.sh info dev                   # one of them in detail, and whether an update waits
#
#     ./tools/vm.sh update                     # fetch the latest snapshot (tools/fetch-image.sh)
#     ./tools/vm.sh restart dev                # ...and boot it, /var and all
#
#     ./tools/vm.sh snapshot dev before-k8s    # copy the /var disk (the machine stopped)
#     ./tools/vm.sh restore dev before-k8s     # and put it back
#     ./tools/vm.sh rm dev before-k8s          # delete a snapshot; `rm dev` deletes the machine
#     ./tools/vm.sh resize dev 40G             # grow /var; the guest grows its filesystem at boot
#
# Every command takes the machine's name first and defaults to `kaetzli`; it is also the
# guest's hostname. Options to start and run are remembered, so a plain `start` boots the
# machine the way it was last booted:
#
#   --image local|ci|DIR   what to boot. local is this checkout's build (output/rootfs.erofs
#                          from tools/build-image.sh, the kernel in rootfs/boot), ci the one
#                          tools/fetch-image.sh downloaded, DIR any directory holding
#                          rootfs.erofs, rootfs.cmdline, bzImage and extensions/ like CI's.
#                          A new machine takes local when there is a build, ci otherwise.
#   --ext a,b / --no-ext   system extensions to attach (podman, debug, kubernetes), replacing
#                          the remembered list; --ext can be repeated
#   --mem MB, --cpus N     the guest's size (default 2048, 2)
#   --size SIZE            the /var disk of a new machine (default 8G, sparse)
#
# More in README.md, under "Machines".

# What a machine is, in output/vms/<name>/ — none of it is ever written by an image update:
#
#   var.img, var.img.machine-id  /var, and the identity that goes with it (qemu_var_disk
#                          in test/qemu-lib.sh): formatted by the guest on its first boot
#   ssh/                   its key pair, made once and handed to every boot as the
#                          ssh.authorized_keys.root credential, and an ssh_config with
#                          this boot's port; `ssh -F output/vms/<name>/ssh/config kaetzli`
#                          is what `vm.sh ssh` runs, and scp, sftp and rsync -e take it too
#   image/                 the image it boots, copied from its source at every start that
#                          finds the source changed. A copy, because qemu reads the root
#                          for as long as the guest runs, and a rebuild or a fetch writing
#                          over the file under it would be a disk changing under a guest
#                          — and dm-verity failing reads in it
#   config                 the remembered options
#   console.log            this boot's console (console.log.1 the one before), written in
#                          both modes; the first place to look when a login fails
#   qemu.pid               while it runs (its console and QMP sockets are in
#                          $XDG_RUNTIME_DIR, since a socket's path must fit in 108 bytes)
#
# qemu is started with -daemonize and belongs to no shell, so closing the terminal that ran
# `start` does not take the guest with it, and nothing here can leave an orphan that holds
# the disk. A reboot in the guest restarts it in place (QEMU_REBOOT), with the same image,
# key and credentials; a poweroff ends it. An update is picked up by the next start, never
# by a reboot: stop and start (restart) is the update.
#
# Overrides: VM_STATE (default output/vms), ARCH (a new machine's architecture), INIT,
# TIMEOUT (seconds start waits for ssh, default 300). Machines run on qemu's pc machine —
# the one test/qemu-lib.sh wires /var disks and extensions into; tools/boot-firecracker.sh
# and boot-vz.sh are the other VMMs.
set -euo pipefail

cd "$(dirname "$0")/.."
source test/qemu-lib.sh

STATE="${VM_STATE:-output/vms}"
mkdir -p "$STATE"
STATE=$(cd "$STATE" && pwd)   # absolute: it ends up in qemu's command line, which `running` reads
DEFAULT_VM=kaetzli

# " NAME" for a command line shown to the user, nothing for the default machine.
arg() { [ "$NAME" = "$DEFAULT_VM" ] || printf ' %s' "$NAME"; }

die() { echo "error: $*" >&2; exit 1; }
say() { echo ">> $*"; }

usage() { sed -n '2,/^# More in README.md/p' "$0" | sed 's/^# \{0,1\}//'; }

# A hostname, since it becomes one.
valid_name() { [[ "$1" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; }
exists() { valid_name "$1" && [ -f "$STATE/$1/config" ]; }

# --- a machine's state -------------------------------------------------------------------

# Sets NAME and DIR. Every command but create works on a machine that exists.
select_vm() { # name [may-be-new]
    NAME="$1"
    valid_name "$NAME" || die "'$NAME' is not a machine name (lower-case letters, digits and -; it becomes the hostname)"
    DIR="$STATE/$NAME"
    # The console and QMP sockets, somewhere short: a socket's path has to fit in 108
    # bytes, and a checkout can be deeper than that leaves room for.
    RUN="${XDG_RUNTIME_DIR:-/tmp/kaetzli-$(id -u)}/kaetzli-vm-$(printf %s "$DIR" | cksum | cut -d' ' -f1)"
    [ "${2:-}" = new ] || exists "$NAME" || die "no machine '$NAME' (./tools/vm.sh list; ./tools/vm.sh start $NAME makes it)"
}

config_defaults() { VM_IMAGE= VM_ARCH= VM_EXTS= VM_MEM=2048 VM_CPUS=2; }

load_config() {
    config_defaults
    # shellcheck disable=SC1091
    source "$DIR/config"
}

save_config() {
    local v
    for v in VM_IMAGE VM_ARCH VM_EXTS VM_MEM VM_CPUS; do
        printf '%s=%q\n' "$v" "${!v}"
    done > "$DIR/config.new"
    mv -f "$DIR/config.new" "$DIR/config"
}

# The pid of this machine's qemu, if it runs. The pidfile alone is not enough — a pid is
# reused — so the process has to be a qemu with this machine's disk on its command line.
running() {
    local pid
    [ -f "$DIR/qemu.pid" ] || return 1
    pid=$(tr -d ' \n' < "$DIR/qemu.pid")
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    ps -p "$pid" -o args= 2>/dev/null | grep -qF "file=$DIR/var.img," || return 1
    echo "$pid"
}

# Everything that changes a machine's files holds its lock until this script exits, so two
# of them cannot both pin an image or copy a disk. qemu does not inherit it (9>&-).
require_stopped() {
    local pid
    if pid=$(running); then
        die "'$NAME' is running (pid $pid); ./tools/vm.sh stop$(arg) first"
    fi
    if command -v flock >/dev/null; then
        exec 9> "$DIR/.lock"
        flock -n 9 || die "another ./tools/vm.sh is starting or changing '$NAME'"
    fi
    rm -f "$DIR/qemu.pid"
    rm -rf "$RUN"
    private_dir "$(dirname "$RUN")"
    private_dir "$RUN"
}

# private_dir <dir> — make it, and make sure it is ours alone. Without XDG_RUNTIME_DIR the
# parent is /tmp/kaetzli-<uid>, a name anyone can create first, and `mkdir -p` accepts
# theirs in silence; the console (login without a password) and QMP sockets go inside.
private_dir() {
    mkdir -p -m 700 "$1"
    [ ! -L "$1" ] && [ -O "$1" ] ||
        die "$1 is not a directory of yours (a symlink, or made by someone else); remove it"
    [ -z "$(find "$1" -maxdepth 0 -perm -g+w -o -maxdepth 0 -perm -o+w)" ] ||
        die "$1 is writable by others; chmod 700 it or remove it"
}

size_bytes() { stat -c %s "$1" 2>/dev/null || stat -f %z "$1"; }

# A size for truncate, in bytes, to compare two of them: 8G, 512M, 1T or a plain number.
parse_size() {
    local n unit
    [[ "$1" =~ ^([0-9]+)([KMGT]?)$ ]] || die "'$1' is not a size (e.g. 8G, 512M)"
    n=${BASH_REMATCH[1]} unit=${BASH_REMATCH[2]}
    case "$unit" in
        K) n=$((n << 10)) ;; M) n=$((n << 20)) ;; G) n=$((n << 30)) ;; T) n=$((n << 40)) ;;
    esac
    echo "$n"
}

create_vm() { # size
    local bytes
    bytes=$(parse_size "$1")
    [ "$bytes" -ge $((256 << 20)) ] || die "a /var disk of $1 is too small to be useful"
    command -v ssh-keygen >/dev/null || die "ssh-keygen not found (install an openssh client)"
    # Made complete or not at all: the config, written last, is what makes it a machine.
    trap 'rm -rf "${DIR:?}"' EXIT
    mkdir -p "$DIR"
    truncate -s "$bytes" "$DIR/var.img"
    od -An -tx1 -N16 /dev/urandom | tr -d ' \n' > "$DIR/var.img.machine-id"
    mkdir -p -m 700 "$DIR/ssh"
    ssh-keygen -q -t ed25519 -N '' -C "kaetzli-vm-$NAME@$(hostname)" -f "$DIR/ssh/id_ed25519"
    config_defaults
    VM_ARCH=$(normalize_arch "${ARCH:-}")
    if [ "$VM_ARCH" = "$(normalize_arch)" ] && local_build_complete; then VM_IMAGE=local; else VM_IMAGE=ci; fi
    save_config
    trap - EXIT
    say "created '$NAME': /var disk of $1 (sparse), machine ID $(cat "$DIR/var.img.machine-id")"
}

# --- images ------------------------------------------------------------------------------

local_build_complete() {
    [ -f output/rootfs.erofs ] && [ -f output/rootfs.cmdline ] && [ -f rootfs/boot/bzImage ]
}

ci_dir() {
    if [ "$VM_ARCH" = "$(normalize_arch)" ]; then echo output/boot-image; else echo "output/boot-image-$VM_ARCH"; fi
}

# The source files of the machine's image as "<pinned name> <source path>" lines in
# SOURCES, and where they are from, for messages, in SOURCE_DESC. Called in this shell,
# not in $(...), so both reach the caller.
image_sources() {
    local root kernel ext name class
    case "$VM_IMAGE" in
        local)
            [ "$VM_ARCH" = "$(normalize_arch)" ] || die "'$NAME' is $VM_ARCH; a local build is $(normalize_arch)"
            root=output kernel=rootfs/boot/bzImage ext=output
            SOURCE_DESC="this checkout's build (./tools/build-image.sh)" ;;
        ci)
            root=$(ci_dir) kernel="$root/bzImage" ext="$root"
            SOURCE_DESC="CI's image in $root"
            [ ! -f "$root/ci-run" ] || SOURCE_DESC="CI run $(cat "$root/ci-run"), in $root"
            [ ! -f "$root/snapshot" ] || SOURCE_DESC="snapshot $(cat "$root/snapshot"), in $root" ;;
        *)
            root="$VM_IMAGE" kernel="$VM_IMAGE/bzImage" ext="$VM_IMAGE"
            SOURCE_DESC="$VM_IMAGE" ;;
    esac
    SOURCES="rootfs.erofs $root/rootfs.erofs
rootfs.cmdline $root/rootfs.cmdline
bzImage $kernel"
    for name in $VM_EXTS; do
        SOURCES+="
extensions/$name.raw $ext/extensions/$name.raw
extensions/$name.dm-verity $ext/extensions/$name.dm-verity"
        # A confext of the same name only where the extension has one (kubernetes).
        for class in confexts; do
            [ -f "$ext/$class/$name.raw" ] || [ -f "$DIR/image/$class/$name.raw" ] || continue
            SOURCES+="
$class/$name.raw $ext/$class/$name.raw
$class/$name.dm-verity $ext/$class/$name.dm-verity"
        done
    done
}

# The dm-verity root hash, which names an image better than any file's date does.
root_hash() { [ -f "$1" ] || return 0; sed -n 's/.* sha256 \([0-9a-f]\{64\}\) .*/\1/p' "$1" | cut -c1-12; }

# Are the pinned files exactly what the source has now (and nothing else)? Fails with the
# reason on stdout; also fails when the source is not there to compare against. Needs
# image_sources first.
image_current() {
    local pinned src want=0 have
    [ -d "$DIR/image" ] || { echo "nothing pinned yet"; return 1; }
    while read -r pinned src; do
        want=$((want + 1))
        [ -f "$src" ] || { echo "missing $src"; return 1; }
        [ -f "$DIR/image/$pinned" ] || { echo "$pinned is not pinned"; return 1; }
        cmp -s "$src" "$DIR/image/$pinned" || { echo "$src changed"; return 1; }
    done <<< "$SOURCES"
    have=$(find "$DIR/image" -type f ! -name source | wc -l)
    [ "$have" -eq "$want" ] || { echo "the extensions changed"; return 1; }
}

pinned_has_all() {
    local pinned src
    while read -r pinned src; do [ -f "$DIR/image/$pinned" ] || return 1; done
}

# Copy the source into image/, unless it is what is pinned already. When the source is
# gone the pinned copy still boots, if it has what was asked for.
pin_image() {
    local reason pinned src old new tmp="$DIR/image.new"
    local sources
    image_sources
    sources="$SOURCES"
    reason=$(image_current) && { say "image: $(root_hash "$DIR/image/rootfs.cmdline") from $SOURCE_DESC, unchanged"; return 0; }

    local missing=()
    while read -r pinned src; do [ -f "$src" ] || missing+=("$src"); done <<< "$sources"
    if [ "${#missing[@]}" -gt 0 ]; then
        # Booting what is pinned is right only if it is everything asked for.
        if [ -d "$DIR/image" ] && pinned_has_all <<< "$sources"; then
            echo "warning: ${missing[0]} is missing; booting the image pinned at the last start" >&2
            return 0
        fi
        echo "error: cannot boot '$NAME': missing ${missing[*]}" >&2
        case "$VM_IMAGE" in
            local) echo "       (./tools/build-image.sh builds it, or --image ci boots CI's)" >&2 ;;
            ci)    echo "       (./tools/vm.sh update downloads it)" >&2 ;;
        esac
        exit 1
    fi

    rm -rf "$tmp"
    mkdir -p "$tmp/extensions" "$tmp/confexts"
    while read -r pinned src; do
        cp --reflink=auto "$src" "$tmp/$pinned"
    done <<< "$sources"
    # A build or a fetch that was writing a file while it was copied shows up here.
    while read -r pinned src; do
        cmp -s "$src" "$tmp/$pinned" || { rm -rf "$tmp"; die "$src changed while it was copied; is a build or fetch still running?"; }
    done <<< "$sources"
    printf '%s\n%s\n' "$SOURCE_DESC" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$tmp/source"

    old=$(root_hash "$DIR/image/rootfs.cmdline")
    new=$(root_hash "$tmp/rootfs.cmdline")
    rm -rf "$DIR/image.old"
    [ ! -d "$DIR/image" ] || mv "$DIR/image" "$DIR/image.old"
    mv "$tmp" "$DIR/image"
    rm -rf "$DIR/image.old"
    if [ -z "$old" ]; then
        say "image: $new from $SOURCE_DESC"
    elif [ "$old" != "$new" ]; then
        say "image: updated $old -> $new from $SOURCE_DESC; /var is kept"
    else
        say "image: $new from $SOURCE_DESC ($reason)"
    fi
}

# --- booting -----------------------------------------------------------------------------

# Options of start and run, applied to the remembered config. Leaves NAME, DIR and the
# config loaded, the machine created if it was new.
prepare() {
    local name="$DEFAULT_VM" size=8G exts= set_exts=0 image= mem= cpus= e
    if [ $# -gt 0 ] && [[ "$1" != -* ]]; then name="$1"; shift; fi
    while [ $# -gt 0 ]; do
        case "$1" in
            --image)  image="${2:?--image needs local, ci or a directory}"; shift 2 ;;
            --ext)    set_exts=1; exts+=" ${2:?--ext needs a name}"; shift 2 ;;
            --no-ext) set_exts=1; exts=; shift ;;
            --mem)    mem="${2:?--mem needs MB}"; shift 2 ;;
            --cpus)   cpus="${2:?--cpus needs a number}"; shift 2 ;;
            --size)   size="${2:?--size needs a size}"; shift 2 ;;
            *) die "unknown option '$1' (./tools/vm.sh help)" ;;
        esac
    done
    local list=""
    for e in ${exts//,/ }; do
        valid_name "$e" || die "'$e' is not an extension name"
        [[ " $list " == *" $e "* ]] || list+="${list:+ }$e"
    done
    exts="$list"
    [ -z "$mem" ] || [[ "$mem" =~ ^[0-9]+$ ]] || die "--mem is in MB: '$mem'"
    [ -z "$cpus" ] || [[ "$cpus" =~ ^[0-9]+$ ]] || die "--cpus is a number: '$cpus'"

    select_vm "$name" new
    if ! exists "$NAME"; then
        [ ! -e "$DIR" ] || die "$DIR exists but is not a machine; move it out of the way"
        create_vm "$size"
    fi
    require_stopped
    load_config
    case "$image" in
        "") ;;
        local|ci) VM_IMAGE="$image" ;;
        *) [ -d "$image" ] || die "--image $image: no such directory"; VM_IMAGE=$(cd "$image" && pwd) ;;
    esac
    [ "$set_exts" = 0 ] || VM_EXTS="$exts"
    VM_MEM="${mem:-$VM_MEM}" VM_CPUS="${cpus:-$VM_CPUS}"
    # Remembered once they are known to work: a typo in --ext must not stick.
    pin_image
    save_config

    ARCH="$VM_ARCH"
    ROOTFS="$DIR/image/rootfs.erofs" KERNEL="$DIR/image/bzImage"
    ROOT_CMDLINE="$DIR/image/rootfs.cmdline" EXTENSIONS="$DIR/image"
    SYSEXTS="$VM_EXTS" VAR_DISK="$DIR/var.img" MEM="$VM_MEM" CPUS="$VM_CPUS"
    INIT="${INIT:-/usr/lib/systemd/systemd}"
    QEMU_PROFILE=pc
    qemu_setup
    qemu_preflight

    SSH_DIR="$DIR/ssh" SSH_KEEP_KEY=1 qemu_ssh_setup
    QEMU_APPEND_EXTRA+=" systemd.hostname=$NAME"
    QEMU_REBOOT=1

    # The last boot's console stays readable until the one after.
    [ ! -f "$DIR/console.log" ] || mv -f "$DIR/console.log" "$DIR/console.log.1"
}

# A command as root on the machine, for this script's own use.
ssh_vm() { ssh -F "$DIR/ssh/config" -o ConnectTimeout=5 -l root kaetzli "$@"; }

cmd_start() {
    # Starting what runs already is not an error, as long as nothing was asked to change.
    if [ $# -le 1 ] && [[ "${1:-}" != -* ]] && exists "${1:-$DEFAULT_VM}"; then
        select_vm "${1:-$DEFAULT_VM}"
        if running > /dev/null; then
            say "'$NAME' is running already (ssh 127.0.0.1:$(ssh_port)): ./tools/vm.sh ssh$(arg)"
            return 0
        fi
    fi
    prepare "$@"
    QEMU_DISPLAY_ARGS=(
        -display none -monitor none
        -chardev "socket,id=con,path=$RUN/console.sock,server=on,wait=off,logfile=$DIR/console.log"
        -serial chardev:con
    )
    qemu_argv
    "$QEMU" "${QEMU_ARGV[@]}" \
        -qmp "unix:$RUN/qmp.sock,server=on,wait=off" \
        -pidfile "$DIR/qemu.pid" -daemonize < /dev/null 9>&-
    say "'$NAME' started (pid $(cat "$DIR/qemu.pid"), ${VM_MEM} MB, ${VM_CPUS} CPUs, extensions: ${VM_EXTS:-none})"

    local until=$((SECONDS + TIMEOUT))
    printf '>> waiting for ssh on 127.0.0.1:%s ' "$SSH_PORT"
    until ssh_vm true 2>/dev/null; do
        if ! running >/dev/null; then
            echo; echo "error: qemu exited; the console:" >&2; tail -40 "$DIR/console.log" >&2; exit 1
        fi
        if [ "$SECONDS" -ge "$until" ]; then
            echo; echo "error: no ssh after ${TIMEOUT}s; the console ($DIR/console.log):" >&2
            tail -40 "$DIR/console.log" >&2; exit 1
        fi
        printf .
        sleep 2
    done
    echo " up"
    local hint
    hint=$(arg)
    cat <<EOF
   ./tools/vm.sh ssh$hint [--root]   a shell (or: ssh -F $DIR/ssh/config kaetzli)
   ./tools/vm.sh console$hint        the serial console (Ctrl-] to leave)
   ./tools/vm.sh stop$hint
EOF
}

# The same machine in the foreground, console in this terminal: qemu's own mux, so Ctrl-a
# x quits and Ctrl-a c is the monitor, as with tools/boot-qemu.py — and logged as well.
cmd_run() {
    prepare "$@"
    QEMU_DISPLAY_ARGS=(
        -display none
        -chardev "stdio,id=con,mux=on,signal=off,logfile=$DIR/console.log"
        -serial chardev:con -mon chardev=con,mode=readline
    )
    qemu_argv
    say "'$NAME' in the foreground: Ctrl-a x quits; ./tools/vm.sh ssh$(arg) from another terminal"
    "$QEMU" "${QEMU_ARGV[@]}" \
        -qmp "unix:$RUN/qmp.sock,server=on,wait=off" \
        -pidfile "$DIR/qemu.pid" 9>&- || true
    rm -f "$DIR/qemu.pid" "$RUN/qmp.sock"
}

cmd_stop() {
    local force=0 pid until
    [ "${1:-}" != --force ] || { force=1; shift; }
    select_vm "${1:-$DEFAULT_VM}"
    [ "${2:-}" != --force ] || force=1
    pid=$(running) || { say "'$NAME' is not running"; return 0; }
    if [ "$force" = 0 ]; then
        say "powering off '$NAME'"
        # ssh is what is sure to work; the ACPI button through QMP is for a guest without
        # sshd, which it only turns off if its systemd is up.
        ssh_vm systemctl poweroff 2>/dev/null ||
            printf '%s\n' '{"execute":"qmp_capabilities"}' '{"execute":"system_powerdown"}' |
                socat - "UNIX-CONNECT:$RUN/qmp.sock" >/dev/null 2>&1 || true
        until=$((SECONDS + 90))
        while kill -0 "$pid" 2>/dev/null && [ "$SECONDS" -lt "$until" ]; do sleep 1; done
    fi
    if kill -0 "$pid" 2>/dev/null; then
        [ "$force" = 1 ] || echo "warning: '$NAME' did not power off in 90s; killing qemu" >&2
        kill "$pid" 2>/dev/null || true
        while kill -0 "$pid" 2>/dev/null; do sleep 0.2; done
        rm -f "$DIR/qemu.pid" "$RUN/console.sock" "$RUN/qmp.sock"
        say "'$NAME' killed; ext4's journal puts /var right at the next boot"
        return 0
    fi
    rm -f "$DIR/qemu.pid" "$RUN/console.sock" "$RUN/qmp.sock"
    say "'$NAME' stopped"
}

cmd_ssh() {
    local name="$DEFAULT_VM" login=user
    [ "${1:-}" != --root ] || { login=root; shift; }
    if [ $# -gt 0 ] && exists "$1"; then name="$1"; shift; fi
    [ "${1:-}" != --root ] || { login=root; shift; }
    select_vm "$name"
    running >/dev/null || die "'$NAME' is not running (./tools/vm.sh start$(arg))"
    exec ssh -F "$DIR/ssh/config" -l "$login" kaetzli "$@"
}

cmd_console() {
    select_vm "${1:-$DEFAULT_VM}"
    running >/dev/null || die "'$NAME' is not running"
    [ -S "$RUN/console.sock" ] || die "'$NAME' runs in the foreground; its console is that terminal"
    command -v socat >/dev/null || die "attaching needs socat; the console is logged to $DIR/console.log"
    say "the console of '$NAME'; Ctrl-] leaves it running (Enter for a prompt)"
    socat -,rawer,escape=0x1d "UNIX-CONNECT:$RUN/console.sock" || true
    echo
}

# --- inventory ---------------------------------------------------------------------------

disk_usage() { # file — what it takes on the host / what the guest sees
    printf '%s/%s' "$(du -h "$1" | cut -f1)" "$(du -h --apparent-size "$1" | cut -f1)"
}

ssh_port() { awk '$1 == "Port" {print $2; exit}' "$DIR/ssh/config" 2>/dev/null; }

cmd_list() {
    local d pid state port snaps
    printf '%-14s %-9s %-7s %-8s %-14s %-12s %-14s %s\n' NAME STATE PID SSH IMAGE PINNED /VAR EXTENSIONS
    for d in "$STATE"/*/config; do
        [ -f "$d" ] || { echo "(none; ./tools/vm.sh start makes one)"; return 0; }
        select_vm "$(basename "$(dirname "$d")")"
        load_config
        state=stopped pid=- port=-
        if pid=$(running); then state=running port=$(ssh_port); else pid=-; fi
        snaps=0
        [ ! -d "$DIR/snapshots" ] || snaps=$(find "$DIR/snapshots" -mindepth 1 -maxdepth 1 -type d ! -name '.*' | wc -l)
        printf '%-14s %-9s %-7s %-8s %-14s %-12s %-14s %s\n' "$NAME" "$state" "$pid" "$port" \
            "$(basename "$VM_IMAGE")" "$(root_hash "$DIR/image/rootfs.cmdline" | grep . || echo -)" \
            "$(disk_usage "$DIR/var.img")$([ "$snaps" = 0 ] || echo " +${snaps}s")" "${VM_EXTS:--}"
    done
}

cmd_info() {
    local pid reason s
    select_vm "${1:-$DEFAULT_VM}"
    load_config
    echo "machine     $NAME ($VM_ARCH, ${VM_MEM} MB, ${VM_CPUS} CPUs) in $DIR"
    if pid=$(running); then
        echo "state       running, pid $pid, up $(ps -o etime= -p "$pid" | tr -d ' '), ssh 127.0.0.1:$(ssh_port)"
    else
        echo "state       stopped"
    fi
    echo "/var        $DIR/var.img, $(disk_usage "$DIR/var.img") (used/size), machine ID $(cat "$DIR/var.img.machine-id")"
    echo "extensions  ${VM_EXTS:-none}"
    image_sources
    if [ -f "$DIR/image/source" ]; then
        echo "image       $(root_hash "$DIR/image/rootfs.cmdline"), pinned $(sed -n 2p "$DIR/image/source") from $(sed -n 1p "$DIR/image/source")"
    else
        echo "image       none pinned yet; $SOURCE_DESC"
    fi
    if reason=$(image_current); then
        echo "            up to date with its source"
    else
        echo "            the next start re-pins: $reason"
    fi
    if [ -d "$DIR/snapshots" ]; then
        for s in "$DIR/snapshots"/*/; do
            [ -d "$s" ] || continue
            echo "snapshot    $(basename "$s"): $(sed -n 1p "$s/taken" 2>/dev/null), $(disk_usage "$s/var.img")"
        done
    fi
    echo "console     $DIR/console.log"
}

# --- the disk ----------------------------------------------------------------------------

# Copies that share blocks with the original where the filesystem can (btrfs, XFS), and
# stay sparse where it cannot.
copy_disk() { cp --reflink=auto --sparse=always "$1" "$2"; }

cmd_snapshot() {
    select_vm "${1:-$DEFAULT_VM}"
    local snap="${2:-$(date -u +%Y%m%d-%H%M%S)}"
    valid_name "$snap" || [[ "$snap" =~ ^[0-9]{8}-[0-9]{6}$ ]] || die "'$snap' is not a snapshot name"
    require_stopped
    [ ! -e "$DIR/snapshots/$snap" ] || die "'$NAME' already has a snapshot '$snap'"
    mkdir -p "$DIR/snapshots/.$snap.tmp"
    copy_disk "$DIR/var.img" "$DIR/snapshots/.$snap.tmp/var.img"
    load_config
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ), image $(root_hash "$DIR/image/rootfs.cmdline")" > "$DIR/snapshots/.$snap.tmp/taken"
    mv "$DIR/snapshots/.$snap.tmp" "$DIR/snapshots/$snap"
    say "snapshot '$snap' of '$NAME'"
}

cmd_restore() {
    select_vm "${1:?usage: ./tools/vm.sh restore NAME SNAPSHOT}"
    local snap="${2:?usage: ./tools/vm.sh restore NAME SNAPSHOT}"
    [ -f "$DIR/snapshots/$snap/var.img" ] || die "'$NAME' has no snapshot '$snap'"
    require_stopped
    copy_disk "$DIR/snapshots/$snap/var.img" "$DIR/var.img.restore"
    mv -f "$DIR/var.img.restore" "$DIR/var.img"
    say "'$NAME' /var is snapshot '$snap' again"
}

cmd_resize() {
    select_vm "${1:?usage: ./tools/vm.sh resize NAME SIZE}"
    local new old
    new=$(parse_size "${2:?usage: ./tools/vm.sh resize NAME SIZE}")
    old=$(size_bytes "$DIR/var.img")
    [ "$new" -gt "$old" ] || die "$2 is not larger than the disk is ($((old >> 20)) MiB); shrinking is not supported"
    require_stopped
    truncate -s "$new" "$DIR/var.img"
    say "'$NAME' /var disk is $2; x-systemd.growfs grows the filesystem at its next boot"
}

cmd_rm() {
    local yes=0 a args=()
    for a in "$@"; do
        case "$a" in --yes|-y) yes=1 ;; -*) die "rm: unknown option $a" ;; *) args+=("$a") ;; esac
    done
    [ "${#args[@]}" -ge 1 ] && [ "${#args[@]}" -le 2 ] || die "usage: ./tools/vm.sh rm NAME [SNAPSHOT] [--yes]"
    select_vm "${args[0]}"
    if [ -n "${args[1]:-}" ]; then
        [ -d "$DIR/snapshots/${args[1]}" ] || die "'$NAME' has no snapshot '${args[1]}'"
        rm -rf "${DIR:?}/snapshots/${args[1]}"
        say "deleted snapshot '${args[1]}' of '$NAME'"
        return
    fi
    require_stopped
    if [ "$yes" = 0 ]; then
        [ -t 0 ] || die "deleting '$NAME' and its /var: pass --yes"
        local answer
        read -r -p "delete '$NAME', its /var disk and its snapshots? [y/N] " answer
        [[ "$answer" =~ ^[yY] ]] || { echo "kept"; return; }
    fi
    rm -rf "${DIR:?}" "${RUN:?}"
    say "deleted '$NAME'"
}

cmd_update() {
    ARCH="${ARCH:-}" ./tools/fetch-image.sh
    local d
    for d in "$STATE"/*/config; do
        [ -f "$d" ] || continue
        select_vm "$(basename "$(dirname "$d")")"
        load_config
        [ "$VM_IMAGE" = ci ] && [ -d "$DIR/image" ] || continue
        image_sources
        if image_current > /dev/null; then continue; fi
        if running > /dev/null; then
            say "'$NAME' runs the previous image: ./tools/vm.sh restart$(arg) boots the new one"
        else
            say "'$NAME' boots the new image at its next start"
        fi
    done
}

cmd_restart() {
    local name="$DEFAULT_VM"
    if [ $# -gt 0 ] && [[ "$1" != -* ]]; then name="$1"; shift; fi
    select_vm "$name"
    cmd_stop "$name"
    cmd_start "$name" "$@"
}

command="${1:-help}"
[ $# -eq 0 ] || shift
case "$command" in
    start)    cmd_start "$@" ;;
    run)      cmd_run "$@" ;;
    stop)     cmd_stop "$@" ;;
    restart)  cmd_restart "$@" ;;
    ssh)      cmd_ssh "$@" ;;
    console)  cmd_console "$@" ;;
    list|ls)  cmd_list ;;
    info)     cmd_info "$@" ;;
    update)   cmd_update ;;
    snapshot) cmd_snapshot "$@" ;;
    restore)  cmd_restore "$@" ;;
    resize)   cmd_resize "$@" ;;
    rm)       cmd_rm "$@" ;;
    help|-h|--help) usage ;;
    *) usage >&2; exit 2 ;;
esac
