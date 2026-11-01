#!/usr/bin/env bash
# Is the Debian snapshot both images are pinned to still current?
#
#     ./tools/check-snapshot.sh           # human readable
#     ./tools/check-snapshot.sh --json    # the shape update-packages.yml consumes
#
# The toolchain answer to tools/check-updates.sh, and deliberately a separate script:
# check-updates.sh iterates packages/*/env.sh and its contract is "every package", which
# the Debian base is not. What the two share is the JSON shape, so the workflow can
# concatenate them and open one pull request per entry with no idea which script produced
# which.
#
# Any newer dated image is reported. How often that turns into a bump is the caller's
# cadence, not this script's: update-packages.yml asks on Sundays only, because every
# bump invalidates the builder tag and with it every package's build cache on both
# architectures — a full rebuild, worth spending weekly for a current toolchain (sid's
# Go, above all, which compiles every Go binary the extensions ship) and not nightly.
#
#   STALE_DAYS    how old the pin has to be before this says so loudly (default 90).
#                 The failure mode of automation is silence: a checker that stops finding
#                 updates and a checker that stops running look identical from the outside
#                 until somebody notices the toolchain is a year old.
set -euo pipefail

cd "$(dirname "$0")/.."

STALE_DAYS="${STALE_DAYS:-90}"

HUB_TAGS="https://hub.docker.com/v2/repositories/library/debian/tags?page_size=100&name=sid-"

JSON=false
case "${1:-}" in
    --json) JSON=true ;;
    -h|--help) sed -n '2,5p' "$0" | sed 's/^# \?//'; exit 0 ;;
    "") ;;
    *) echo "error: unknown option: $1" >&2; exit 2 ;;
esac

emit_empty() { $JSON && echo '[]'; return 0; }

pinned_in() { sed -n -E 's|^ARG SNAPSHOT=([0-9]{8}).*|\1|p' "$1" | head -n1; }

builder_date=$(pinned_in builder/Containerfile)
image_date=$(pinned_in image/Containerfile)

if [ -z "$builder_date" ] || [ -z "$image_date" ]; then
    echo "error: no 'ARG SNAPSHOT=<yyyymmdd>' line in builder/Containerfile or image/Containerfile" >&2
    exit 1
fi

# Worth checking on its own, and nothing else would: the two images are pinned separately
# and are supposed to be pinned together. If they drift apart, the toolchain and the thing
# that lays out the filesystem were built against different Debians, and every size
# comparison across that boundary is measuring two changes at once.
if [ "$builder_date" != "$image_date" ]; then
    echo "error: builder/Containerfile is pinned to $builder_date but image/Containerfile to $image_date" >&2
    echo "       they are bumped together; ./tools/bump-snapshot.sh writes both" >&2
    exit 1
fi

current="$builder_date"
# yyyymmdd is not a date format `date -d` reads, so spell it out.
as_date() { printf '%s-%s-%s' "${1:0:4}" "${1:4:2}" "${1:6:2}"; }
age_days=$(( ( $(date -u +%s) - $(date -u -d "$(as_date "$current")" +%s) ) / 86400 ))

latest=$(curl -fsS --retry 3 --max-time 60 "$HUB_TAGS" \
    | jq -r '.results[].name | select(test("^sid-[0-9]{8}$"))' \
    | sed 's/^sid-//' | sort | tail -n1) || latest=

if [ -z "$latest" ]; then
    echo "error: could not list dated sid tags on Docker Hub" >&2
    exit 1
fi

# Said before the up-to-date answer, not after: a pin nobody has moved in three months is
# worth hearing about whether or not a newer image happens to exist this week.
if [ "$age_days" -ge "$STALE_DAYS" ]; then
    echo "warning: the Debian snapshot pin is $age_days days old ($current)" >&2
    echo "         bumps are proposed automatically, so this means they are not landing" >&2
fi

if [ "$(printf '%s\n%s\n' "$current" "$latest" | sort | tail -n1)" = "$current" ]; then
    $JSON || printf '%-16s %-10s up to date (%d days old)\n' debian-snapshot "$current" "$age_days"
    emit_empty
    exit 0
fi


url="https://hub.docker.com/_/debian/tags?name=sid-$latest"

$JSON || printf '%-16s %-10s -> %-10s %s\n' debian-snapshot "$current" "$latest" "$url"
$JSON && jq -nc --arg c "$current" --arg l "$latest" --arg u "$url" \
    '[{package:"debian-snapshot", current:$c, latest:$l, url:$u}]'

exit 0
