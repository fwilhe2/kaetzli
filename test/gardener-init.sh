#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

image="europe-docker.pkg.dev/gardener-project/releases/gardener/node-agent:v1.154.6@sha256:4706369040b1fa551549e05d3f85c2c651ed3f486ea079f80c7e064aceb425e6"

echo "> Prepare temporary directory for image pull and mount"
tmp_dir="$(mktemp -d)"
unmount() {
  ctr images unmount "$tmp_dir" && rm -rf "$tmp_dir"
}
trap unmount EXIT

echo "> Pull gardener-node-agent image and mount it to the temporary directory"
CTR_MAJOR=$(ctr version | grep Version | tail -n1 | awk '{print $2}' | cut -d '.' -f 1 | sed 's/[a-zA-Z]//g')
CTR_EXTRA_ARGS=""
if [ "$CTR_MAJOR" -gt 1 ]; then
    CTR_EXTRA_ARGS="--skip-metadata"
fi
ctr images pull $CTR_EXTRA_ARGS --hosts-dir "/etc/containerd/certs.d" "$image"
ctr images mount "$image" "$tmp_dir"

echo "> Copy gardener-node-agent binary to host (/opt/bin) and make it executable"
mkdir -p "/opt/bin"
cp -f "$tmp_dir/gardener-node-agent" "/opt/bin" || cp -f "$tmp_dir/ko-app/gardener-node-agent" "/opt/bin"
chmod +x "/opt/bin/gardener-node-agent"

echo "> Bootstrap gardener-node-agent"
exec "/opt/bin/gardener-node-agent" bootstrap --config-dir="/var/lib/gardener-node-agent"
