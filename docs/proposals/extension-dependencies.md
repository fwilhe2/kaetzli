# Build dependencies between packages

**Status: implemented** for the kiosk extension, the first real case (rather than the
libpcap pilot suggested below). `DEPENDS=` in `env.sh`; `package_depends` in
`tools/lib.sh` resolves and checks it (unknown packages, cycles, edges into another
extension); `build.sh` merges the closure's staging trees over glibc into
`staging/.sysroot-<pkg>`, rewrites their `.pc` files to point inside it, passes
`PKG_CONFIG_PATH`, and fails a build whose installed files name that sysroot. The
extension assembler drops link-time files. CI builds an extension's chained packages in
one job (`tools/build-chain.sh`, ci.yml's `chain`), as suggested below; levels are not
needed yet.

## Current model

Each package builds independently against the glibc sysroot and development libraries in
the pinned builder image. At runtime, it uses libraries from the base image or from its
own extension. Extension packages cannot rely on a different extension: users may attach
them independently.

Libraries with a single consumer can be vendored and built statically. This works for leaf
dependencies, but not for larger stacks with shared libraries built in this repository.

## Proposed model

Add a `DEPENDS=` field to `packages/<name>/env.sh`. Dependencies must be packages in the
base or in the same extension. Reject cycles and references to other extensions.

For each package build, assemble a read-only dependency sysroot from the declared packages'
staging trees. Put it after the glibc sysroot and before the builder's headers and libraries.
Set `PKG_CONFIG_PATH` to the staged `.pc` files and enable pkgconf path relocation so their
`/usr` prefixes resolve inside the sysroot. Do not set `LD_LIBRARY_PATH` globally; use it
only for build tools that must run newly built libraries.

The extension assembler would also need to remove headers, static libraries, and pkg-config
files from runtime images, as the base assembler already does.

## CI implications

Dependencies create a build graph. CI must build dependencies before dependents and include
their inputs in cache keys. Two approaches are possible:

- Build topological levels in separate matrix jobs. This preserves per-package parallelism
  but adds workflow complexity and lengthens the critical path.
- Build each extension's packages in order in one job. This is simpler, but a change to one
  package rebuilds the extension's full package set.

Start with one job per extension. Add level-based jobs only if build time justifies them.

## When to implement

Implement this only when a real extension needs a non-leaf library built here. A useful
first case would move libpcap from tcpdump's vendored source into the debug extension, then
build tcpdump against it through the new sysroot. That would exercise dependency resolution,
pkg-config relocation, caching, and runtime trimming with one dependency edge.
