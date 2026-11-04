#!/usr/bin/env bash
# Run ci.yml on a branch whose head was pushed with GITHUB_TOKEN, and report the result on
# its pull request as a check run:
#
#     ./tools/dispatch-ci.sh <branch> <pr-url>
#
# Prints the run's conclusion and exits 0 only when it is `success`.
#
# GitHub starts no workflow run for a push made with GITHUB_TOKEN, so this asks for one:
# workflow_dispatch is the event the token is allowed to trigger. The run belongs to the
# branch rather than to the pull request, which is why the result has to be copied onto
# the pull request by hand, as the "Package update CI" check. And workflow_run is not
# emitted for a workflow dispatched with this token, so nothing can be told when the run
# ends — the caller stays alive until it does, which keeps the check from being left
# pending forever.
#
# Shared by update-packages.yml (a freshly opened pull request) and auto-merge.yml (one
# rebased onto main), which is the reason it is a script: both have to find the run
# they dispatched and not an older one, and only the second has an older one to find.
set -euo pipefail

cd "$(dirname "$0")/.."

[ $# -eq 2 ] || { echo "usage: $0 <branch> <pr-url>" >&2; exit 2; }
BRANCH=$1
PR_URL=$2
REPO=${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}

# The pushed head, not the local one: it is what the dispatched run will check out.
SHA=$(git ls-remote origin "refs/heads/$BRANCH" | cut -f1)
[ -n "$SHA" ] || { echo "error: no branch $BRANCH on origin" >&2; exit 1; }

complete_check() {
    gh api --method PATCH "repos/$REPO/check-runs/$CHECK_ID" \
        -f status=completed \
        -f conclusion="$1" \
        -f details_url="$2" \
        -f 'output[title]=Package update CI' \
        -f "output[summary]=$3" \
        >/dev/null
}

CHECK_ID=$(gh api --method POST "repos/$REPO/check-runs" \
    -f name='Package update CI' \
    -f head_sha="$SHA" \
    -f status=in_progress \
    -f details_url="$PR_URL" \
    --jq '.id')
echo "created check run $CHECK_ID on $SHA" >&2

gh workflow run ci.yml --ref "$BRANCH" >&2

# Matched on the commit as well as the branch: after a rebase the branch already has a
# finished run from its previous head, and `--limit 1` on the branch alone can return it
# before the new one is listed — which would report the old result for the new commit.
RUN_ID=""
RUN_URL=""
for _ in $(seq 12); do
    sleep 5
    RUN=$(gh run list --workflow ci.yml --branch "$BRANCH" --commit "$SHA" \
        --event workflow_dispatch --limit 1 \
        --json databaseId,url --jq '.[0] | "\(.databaseId) \(.url)"' || true)
    if [ -n "$RUN" ]; then
        read -r RUN_ID RUN_URL <<< "$RUN"
        break
    fi
done

if [ -z "$RUN_ID" ]; then
    echo "::warning::dispatched ci.yml for $BRANCH but no run showed up within a minute"
    complete_check failure "$PR_URL" 'CI did not start after it was dispatched'
    echo failure
    exit 1
fi

gh pr comment "$PR_URL" \
    --body "CI for \`${SHA:0:12}\`: $RUN_URL (the Package update CI check above will be updated when it finishes)" >&2

# gh run watch can return before the run has finished (an API hiccup), and an empty
# conclusion is refused by the check-runs API, leaving the check in progress forever.
gh run watch "$RUN_ID" --exit-status >&2 || true
until [ "$(gh run view "$RUN_ID" --json status --jq .status)" = completed ]; do sleep 60; done
CONCLUSION=$(gh run view "$RUN_ID" --json conclusion --jq '.conclusion')

complete_check "$CONCLUSION" "$RUN_URL" "CI finished with conclusion: $CONCLUSION"
echo "updated check $CHECK_ID: $CONCLUSION" >&2

echo "$CONCLUSION"
[ "$CONCLUSION" = success ]
