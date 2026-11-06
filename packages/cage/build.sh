meson_install -Dman-pages=disabled

# ---------------------------------------------------------------------------------
# The kiosk session (docs/proposals/kiosk.md): cage on tty1 as the unprivileged `kiosk`
# user, with logind handing it the screen and the input devices. Shipped here, as the
# other extensions' packages ship their units (packages/cri-o), since cage is what the
# unit runs. The application here is foot; packages/wpewebkit replaces it with the
# browser through a drop-in (kiosk.service.d/10-browser.conf).

# The user, as a userdb record rather than a sysusers.d line: the extension is merged
# after /etc was sealed at build time, so nothing may add it to /etc/passwd. nss-systemd
# (nsswitch.conf: passwd/group/shadow `files systemd`) serves drop-in records from
# /usr/lib/userdb, by name and, through the links, by number. No password: nothing logs
# in as it; the unit below starts its session. bash is its shell because foot runs it.
install -d "$ROOTFS/usr/lib/userdb"
cat > "$ROOTFS/usr/lib/userdb/kiosk.user" <<'JSON'
{
	"userName" : "kiosk",
	"uid" : 1100,
	"gid" : 1100,
	"realName" : "Kiosk",
	"homeDirectory" : "/var/lib/kiosk",
	"shell" : "/usr/bin/bash",
	"disposition" : "regular"
}
JSON
cat > "$ROOTFS/usr/lib/userdb/kiosk.group" <<'JSON'
{
	"groupName" : "kiosk",
	"gid" : 1100,
	"disposition" : "regular"
}
JSON
ln -s kiosk.user "$ROOTFS/usr/lib/userdb/1100.user"
ln -s kiosk.group "$ROOTFS/usr/lib/userdb/1100.group"

# The session. PAMName= makes it a logind session (pam_systemd), and TTYPath= puts that
# session on tty1 of seat0, which is what lets libseat take the DRM and input devices
# without root. Conflicts= takes tty1 from a getty. -s keeps VT switching, so tty2's
# getty is reachable (admin mode). AssertPathExists=: booted on the default kernel, which
# has no screen, this is a failed unit, which test/systemd.py would report, rather than a
# silent skip (docs/proposals/kiosk.md, *Kernel flavours*). Restart=always, since the
# session ends whenever the application does. WLR_RENDERER=pixman: wlroots is built with
# no GPU renderer, and its automatic choice only falls back to pixman when the GPU has no
# render node, which virtio-gpu has (renderD128) — so `auto` finds nothing and cage exits.
# LANG=C.UTF-8: foot needs a UTF-8 locale, which the extension carries (packages/foot).
install -d "$ROOTFS/usr/lib/systemd/system/multi-user.target.d"
cat > "$ROOTFS/usr/lib/systemd/system/kiosk.service" <<'UNIT'
[Unit]
Description=Kiosk session (cage on tty1)
Documentation=https://github.com/fwilhe2/kaetzli/blob/main/docs/proposals/kiosk.md
AssertPathExists=/dev/dri/card0
Wants=kiosk-udev.service dbus.socket systemd-logind.service
After=kiosk-udev.service dbus.socket systemd-logind.service systemd-user-sessions.service
Conflicts=getty@tty1.service
After=getty@tty1.service

[Service]
User=kiosk
PAMName=kiosk
StateDirectory=kiosk
WorkingDirectory=~
Environment=XDG_SESSION_TYPE=wayland
Environment=WLR_RENDERER=pixman
Environment=LANG=C.UTF-8
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty-fail
StandardOutput=journal
StandardError=journal
UtmpIdentifier=tty1
UtmpMode=user
ExecStart=/usr/bin/cage -s -- /usr/bin/foot
Restart=always
RestartSec=2
UNIT

# libinput's udev rules (device groups, fuzz overrides) arrive with the merge, after
# udevd has already handled the devices. Reloaded and replayed for input and DRM before
# the session starts; nothing here ships hwdb files, which could not be rebuilt on a
# read-only /usr.
cat > "$ROOTFS/usr/lib/systemd/system/kiosk-udev.service" <<'UNIT'
[Unit]
Description=Apply the kiosk extension's udev rules
After=systemd-udevd.service systemd-sysext.service
Requires=systemd-udevd.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/udevadm control --reload
ExecStart=/usr/bin/udevadm trigger --action=change --subsystem-match=input --subsystem-match=drm --settle
UNIT

# Upholds=, not a .wants link: the extension is merged during sysinit, after the boot
# transaction was computed (docs/guides/sysext.md).
cat > "$ROOTFS/usr/lib/systemd/system/multi-user.target.d/10-kiosk.conf" <<'UNIT'
[Unit]
Upholds=kiosk.service
UNIT

# Its PAM service, in the confext (pam.d/other is pam_deny). No auth: the unit starts the
# session, nobody types a password into it.
install -d "$ROOTFS/etc/pam.d"
cat > "$ROOTFS/etc/pam.d/kiosk" <<'PAM'
# The kiosk session, started by kiosk.service as the kiosk user. pam_systemd registers it
# with logind as a session on seat0, which is what gives cage the screen and the input
# devices; required, since without it there is no session to take them from.
account    required   pam_unix.so
session    required   pam_unix.so
session    required   pam_systemd.so
PAM
