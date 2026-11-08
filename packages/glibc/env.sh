VERSION="2.44"
PACKAGE="glibc-$VERSION"
TARBALL="$PACKAGE.tar.gz"
URL="https://ftp.fau.de/gnu/glibc/${TARBALL}"
SIG_URL="${URL}.sig"
MIRRORS="https://ftp.gnu.org/gnu/glibc/${TARBALL}"
SHA256="1217fc41ac7fb1f310c8c32b9c6c009cc769398d4b367b6aa57be0bb3ea8c1ef"
LICENSE="LGPL-2.1-or-later AND GPL-2.0-or-later"
CPE="cpe:2.3:a:gnu:glibc:${VERSION}:*:*:*:*:*:*:*"
# The release branch, not the release: glibc backports its fixes — security ones above
# all — to release/<version>/master and tags no tarball for them until the next point
# release, which most series never get. The tarball plus `git format-patch` of the branch
# since the tag is the branch's tree at its head (patches/upstream/, tools/sync-patches.sh);
# Debian builds the same thing, as debian/patches/git-updates.diff.
UPSTREAM_PATCH_GIT="https://sourceware.org/git/glibc.git"
UPSTREAM_PATCH_BRANCH="release/${VERSION}/master"
UPSTREAM_PATCH_BASE="glibc-${VERSION}"
# Which of those commits fix which CVE, as the advisories on master record it, so the CVE
# scan reports them as patched (tools/patch-cves.sh).
UPSTREAM_PATCH_ADVISORIES="master:advisories"
# glibc is what every other package is then compiled against, so it is the one package
# that has to build against the builder image's own libc.
NO_SYSROOT=1
