# meson_install supplies --buildtype=release; -Dmode=release is about log and status
# format, not optimization. -Dc_args replaces CFLAGS rather than adding to it, so the
# sysroot flags are passed by hand (SYSROOT_CFLAGS, without the -O2 that would override
# the buildtype). -Wno-error=override-init: systemd promotes that warning specifically,
# and sid's compiler trips it in generated code.
#
# seccomp is what makes SystemCallFilter= in systemd's own units do anything; pam builds
# pam_systemd, which registers logins with logind. The rest is the trim: each component
# off is a binary, its units and its D-Bus policy gone, and one less way to degrade a boot.
systemd_opts=(
    -Dmode=release
    -Dc_args="${SYSROOT_CFLAGS:-} -Wno-error=override-init"

    -Dselinux=disabled -Dseccomp=enabled -Daudit=disabled
    -Dpam=enabled -Dopenssl=disabled -Dlibcryptsetup=disabled

    # The EFI stub, for the signed UKI image/build-rootfs.sh makes for Secure Boot
    # (docs/guides/secure-boot.md); nothing in the image boots through it, so the
    # assembly removes it and systemd-boot from the tree after. No ESP, so no
    # kernel-install; ukify is python, so it is the assembler's (image/Containerfile).
    -Dbootloader=enabled -Defi=true -Dsbat-distro=kaetzli
    -Dsbat-distro-summary=Kaetzli -Dsbat-distro-pkgname=systemd -Dsbat-distro-version="$KAETZLI_VERSION"
    -Dsbat-distro-url=https://github.com/fwilhe2/kaetzli
    -Dkernel-install=false -Dukify=disabled
    -Dtpm=false -Dtpm2=disabled

    # Hardware a VM does not have, or that only matters on a laptop.
    -Dbacklight=false -Drfkill=false -Dhibernate=false -Dquotacheck=false
    -Dvconsole=false -Dpstore=false -Dbinfmt=false -Dxdg-autostart=false

    # 22 MB describing devices a virtio guest never sees.
    -Dhwdb=false

    # C-locale only.
    -Dlocaled=false -Dtranslations=false

    # systemd's own container and image machinery: crun and podman are the runtime, and
    # podman takes its ID ranges from /etc/subuid.
    -Dmachined=false -Dnspawn=disabled -Dvmspawn=disabled -Dimportd=disabled
    -Dnss-mymachines=disabled -Dportabled=false
    -Dmountfsd=false -Dnsresourced=false

    # How software is added to the immutable image (docs/guides/sysext.md). Verity is set up by
    # the kernel (dm-init), so no libcryptsetup.
    -Dsysext=true

    # Deployment and provisioning. firstboot: the image ships a complete /etc. fdisk:
    # repart, sysupdate and homed were its only callers.
    -Drepart=disabled -Dsysupdate=disabled -Dsysinstall=false -Dfirstboot=false
    -Dimds=disabled -Dhomed=disabled -Dstoragetm=false -Doomd=false
    -Dfdisk=disabled

    # coredump and journal-remote need libraries the image does not ship.
    -Dcoredump=false -Delfutils=disabled -Dremote=disabled

    # Optional libraries the builder has and the image does not, off by name.
    -Dlibcurl=disabled -Dgnutls=disabled -Dmicrohttpd=disabled -Dgcrypt=disabled
    -Dp11kit=disabled -Dlibfido2=disabled -Dqrencode=disabled -Dlibarchive=disabled
    -Dxkbcommon=disabled -Dpcre2=disabled -Dglib=disabled -Dxenctrl=disabled
    -Dpwquality=disabled -Dpasswdqc=disabled
    -Dbzip2=disabled -Dlz4=disabled -Didn=false -Dlibidn2=disabled

    # Access control with no userspace behind it: no polkitd, no SMACK/IMA/IPE/AppArmor.
    -Dpolkit=disabled -Dsmack=false -Dima=false -Dipe=false -Dapparmor=disabled

    -Dtests=false

    # Macros for building rpm packages against systemd; there is no rpm.
    -Drpmmacrosdir=no
)

meson_install "${systemd_opts[@]}"

# bootctl: it manages an EFI system partition and the boot loader's variables, and a VM
# here has neither. And what -Dbootloader brings besides the stub (above): systemd-boot
# and the addon stub, boot counting (bless-boot), the random seed and update services that
# run bootctl, its varlink socket. Only linux<arch>.efi.stub stays, for the UKI.
drop_installed bootctl
case "$(uname -m)" in x86_64) efi_arch=x64 ;; aarch64) efi_arch=aa64 ;; esac
drop_installed \
    "usr/lib/systemd/boot/efi/systemd-boot$efi_arch.efi" \
    "usr/lib/systemd/boot/efi/addon$efi_arch.efi.stub" \
    usr/lib/systemd/systemd-bless-boot \
    usr/lib/systemd/systemd-boot-check-no-failures \
    usr/lib/systemd/system-generators/systemd-bless-boot-generator \
    usr/lib/systemd/system/systemd-bless-boot.service \
    usr/lib/systemd/system/systemd-boot-check-no-failures.service \
    usr/lib/systemd/system/systemd-boot-random-seed.service \
    usr/lib/systemd/system/sysinit.target.wants/systemd-boot-random-seed.service \
    usr/lib/systemd/system/systemd-boot-clear-sysfail.service \
    usr/lib/systemd/system/systemd-boot-update.service \
    usr/lib/systemd/system/systemd-bootctl@.service \
    usr/lib/systemd/system/systemd-bootctl.socket \
    usr/lib/systemd/system/sockets.target.wants/systemd-bootctl.socket \
    usr/lib/systemd/system/systemd-sysupdate-notify-bootctl@.service \
    usr/lib/systemd/system/systemd-sysupdate-notify-bootctl.socket \
    usr/lib/tmpfiles.d/20-systemd-stub.conf

# udev helpers and rules for hardware the kernel cannot have: each subsystem below is
# cleared by packages/kernel's vm.config on both architectures, and that build fails if
# one comes back. No meson switch exists for any of them. What a VM can still present
# stays — virtio-scsi disks (scsi_id, and ata_id for a passed-through SATA disk),
# virtio-input (the input, mouse and touchpad rules), gpio-keys on arm64, DMI.
#   ATAPI/SCSI CD-ROM (BLK_DEV_SR)  cdrom_id, 60-cdrom_id
#   MTD                             mtd_probe, 75-probe_mtd, 60-persistent-storage-mtd
#   media (MEDIA_SUPPORT)           v4l_id, 60-persistent-v4l, 70-camera,
#                                   60-persistent-media-controller
#   INPUT_JOYSTICK                  70-joystick
#   SOUND                           60-persistent-alsa, 78-sound-card
#   DRM                             60-drm
#   BTRFS_FS                        64-btrfs
#   IIO                             60-sensor
#   USB_SUPPORT, with hwdb off      60-autosuspend
drop_installed usr/lib/udev/cdrom_id usr/lib/udev/mtd_probe usr/lib/udev/v4l_id
for rule in 60-cdrom_id 75-probe_mtd 60-persistent-storage-mtd 60-persistent-v4l 70-camera \
            60-persistent-media-controller 70-joystick 60-persistent-alsa 78-sound-card \
            60-drm 64-btrfs 60-sensor 60-autosuspend; do
    drop_installed usr/lib/udev/rules.d/$rule.rules
done

# The MIME type for systemd's own file formats, for a shared-mime-info database the image
# does not have; meson installs it unconditionally.
drop_installed usr/share/mime
