VERSION="1.0"
PACKAGE="user-data-${VERSION}"
LICENSE="MIT"
# Ours, in packages/user-data/src: a unit and a bash script that fetch the instance's
# user data from a cloud's metadata service and run it. cloud-init is the usual answer
# and is Python (CLAUDE.md, constraint 5); what a node needs from it here is one HTTP GET
# and an exec, which curl and bash already in the image do.
LOCAL_SOURCE=1
