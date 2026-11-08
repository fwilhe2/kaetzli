# ps, top, free, pgrep/pkill, sysctl, vmstat, watch, uptime and w: the tools for asking a
# running machine what it is doing. Until this the guest could only answer that from
# /proc by hand, which is what made debugging a misbehaving dockerd in it so slow.
#
# procps-ng is the only candidate that keeps the stack GNU-shaped. It is the maintained
# continuation of procps (the -ng fork is from 2011 and is now simply upstream), shipped
# as the default by Debian, Fedora, Arch and Alpine alike; busybox's applets are out by
# constraint 1. Releases are on SourceForge, where upstream still publishes the dist
# tarball — the GitLab tag archives have no generated configure.
VERSION="4.0.7"
PACKAGE="procps-ng-${VERSION}"
TARBALL="$PACKAGE.tar.xz"
URL="https://downloads.sourceforge.net/project/procps-ng/Production/${TARBALL}"
SIG_URL="${URL}.asc"
SHA256="9d2021f47a4501c667862c9942a92d1953694b21d11bcd1702e83eb594e3d67d"
# The programs are GPL-2.0-or-later and libproc2 is LGPL-2.1-or-later, which ps and watch
# share; top alone still carries the older "Library General Public License" text, version
# 2 or any later version.
LICENSE="GPL-2.0-or-later AND LGPL-2.1-or-later AND LGPL-2.0-or-later"
CPE="cpe:2.3:a:procps-ng_project:procps-ng:${VERSION}:*:*:*:*:*:*:*"
UPSTREAM_INDEX="https://sourceforge.net/projects/procps-ng/files/Production/"
