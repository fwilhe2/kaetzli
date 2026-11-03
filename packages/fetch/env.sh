VERSION="1.0"
PACKAGE="fetch-${VERSION}"
LICENSE="MIT"
# No TARBALL, URL or SHA256: this package's source is in this repository, under
# packages/fetch/src, and LOCAL_SOURCE is what tells the build machinery so. There is
# nothing to download, nothing to vendor into the sources image, and no upstream to check
# for releases — VERSION is ours to bump and means only what we say it means.
#
# See "Packages whose source is in this repository" in CLAUDE.md for what that flag
# changes, and packages/fetch/build.sh for the one constraint it imposes: the source
# directory is mounted read-only, so the build may not write into it.
LOCAL_SOURCE=1
