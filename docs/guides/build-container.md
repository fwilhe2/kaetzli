# Build inputs

The build has a networked preparation phase and an offline package-build phase. Preparation
uses pinned inputs to make two OCI images: a builder image and a sources image. Package
builds run with `--network=none`.

## Builder image

`builder/Containerfile` installs the explicit package list in `builder/deps.txt` from a
pinned Debian snapshot. The image is built natively for each supported architecture. Its
content-addressed tag includes the Containerfile, dependency list, build entrypoint, and
base image reference; see `tools/image-tags.sh`.

The explicit dependency list makes the toolchain reviewable and repeatable. Avoid adding a
dependency merely because an upstream package can use it optionally: configure that feature
out when possible. Otherwise the build may link against a library that the target image
does not contain.

## Sources image

The `FROM scratch` sources image contains the pinned source archives for all packages. Its
tag hashes the archive names and checksums. The same architecture-independent image is
published for amd64 and arm64.

`tools/fetch-sources.sh` verifies downloaded archives against the SHA256 values in each
package's `env.sh`. When a Debian pool URL has been removed, it falls back to
snapshot.debian.org. `tools/prep.sh` pulls the content-addressed images when available,
building them from their pinned inputs on a cache miss. It extracts source archives into
`downloads/` for local builds.

A miss is usually one bumped package, so before downloading anything `prep.sh` takes the
archives the last pushed sources image (`sources:latest`) already has. They are matched by
SHA256 against the pins, not by name, and verified again; only the rest come from upstream.
`FRESH=1` skips this, to prove every upstream still serves the pinned bytes.

## Use

`./build.sh <package>` prepares the required inputs if needed, then builds the package
offline. To prepare before disconnecting from the network, run:

```sh
./tools/prep.sh
```

CI prepares and publishes the inputs, then builds packages and assembles the images. The
image-assembly tools (`image/Containerfile`, also pinned to the snapshot) are a third
content-addressed image: `./tools/prep.sh assembler` pulls or builds it, and
`tools/build-image.sh` calls that itself. Set
`FRESH=1` when running preparation to rebuild both images rather than reuse cached tags;
`./tools/prep.sh --push` pushes newly built images.

For the full build flow, see [How the build works](how-it-works.md). For the recipe archive
proposal, see [Build reproduction](../proposals/reproducing-a-build.md).
