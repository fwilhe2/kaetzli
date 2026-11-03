#!/usr/bin/env bash
# Merge the pull requests update-packages.yml opened, one at a time, each only after CI
# has passed on exactly the commit that lands on main:
#
#     ./tools/auto-merge.sh        # what .github/workflows/auto-merge.yml runs
#     ./tools/auto-merge.sh -n     # say what would happen, touch nothing (works locally)
#
# Green means one thing: the "Package update CI" check, which tools/dispatch-ci.sh writes
# from ci.yml's run, concluded `success` on the very commit that is pushed to main, and
# nothing else on it failed. Other checks passing is not CI passing, and a neutral or
# skipped CI is not a pass. The checks are read together with the commit they belong to,
# and a branch that has moved since is not merged.
#
# Green on its own is not enough either, and that is most of this script. A bump's CI ran
# against main as it was when the pull request was opened, and the updater opens several
# at once — so after the first is merged, every other one was tested against a main that
# no longer exists. Each one is therefore rebased onto the current main and built again
# before it is merged, unless main has not moved since its last green run. The merge is a
# fast-forward push of that tested commit, not a merge button: a rebase or squash merge
# writes a new commit, and a merge commit a new tree, neither of which CI has seen. A push
# that is not a fast-forward is refused, which is what makes a human pushing to main
# mid-run safe — that pull request simply waits for the next run.
#
# A pull request is left alone, for a human, when:
#   - it is not from github-actions on an update/ branch — this merges bumps, not changes
#   - it carries the `hold` label — the way to say "not this one" without closing it
#   - any check on it failed — a red build needs work in build.sh, not a retry
#   - a check is still running and started recently — someone else's CI is in flight
#   - the rebase conflicts, or the rebuilt CI fails
#   - it was labelled `hold` or closed while it was being built: the label and the state
#     are read again just before the merge, since a rebuild takes an hour
#
# There is no minimum age. The updater opens pull requests at 17:00 UTC and nothing is
# merged before 20:00, which is the time to look and label; a mistake that gets through
# is a revert. Closing a pull request skips only that night: update-packages.yml opens
# it again the next evening, and `hold` is what keeps it out.
#
# Scheduled runs only act in the evening and at night in Europe: no pull request is
# started outside 20:00–05:00 UTC (22:00–07:00 CEST, 21:00–06:00 CET). GitHub's cron can
# start a run an hour or more late, so the schedule alone does not guarantee that — this
# check does, and it is made before each pull request rather than once, because one
# rebuild can take an hour. A pull request started just before the cutoff can still be
# building at eight in the morning in Germany; that is the accepted cost of a window
# wide enough to survive a late cron. A run started by hand (workflow_dispatch, or
# locally) is somebody choosing to act now, and is not held to the window.
#
# Pushes made with GITHUB_TOKEN start no workflow, which applies to main as much as to a
# branch: once anything was merged, ci.yml is dispatched on main, so publish-oci and the
# caches on main follow what landed.
set -euo pipefail

cd "$(dirname "$0")/.."

DRY_RUN=
if [ "${1:-}" = -n ]; then DRY_RUN=1; shift; fi
[ $# -eq 0 ] || { echo "usage: $0 [-n]" >&2; exit 2; }

NIGHT_START_UTC=20
NIGHT_END_UTC=5
HOLD_LABEL=hold
CI_CHECK="Package update CI"
now=$(date +%s)

# run <cmd...> — run it, or in a dry run only say so.
run() {
    if [ -n "$DRY_RUN" ]; then echo "  would run: $*"; else "$@"; fi
}

# checks_state — green, untested, pending, failed, stale or none, from the
# statusCheckRollup on stdin: every check and status on the pull request's head. Green
# needs the CI check run itself to have succeeded; all done without that is untested. A
# check that has been "running" for longer than any CI run takes is not treated as
# running: it is one whose reporter died (a timed-out run of this script leaves its
# check in_progress), and waiting on it would wait forever.
checks_state() {
    jq -r --argjson now "$now" --arg ci "$CI_CHECK" '
        [.statusCheckRollup[] | {
            ci: (.name == $ci and .conclusion == "SUCCESS"),
            done: ((.status // "COMPLETED") == "COMPLETED" and (.state // "") != "PENDING"),
            result: (.conclusion // .state // ""),
            age: ($now - ((.startedAt // "1970-01-01T00:00:00Z") | fromdateiso8601))
        }] as $c
        | if   ($c | length) == 0 then "none"
          elif any($c[]; .done and (.result | IN("SUCCESS", "NEUTRAL", "SKIPPED") | not)) then "failed"
          elif any($c[]; (.done | not) and .age < 6 * 3600) then "pending"
          elif all($c[]; .done) and any($c[]; .ci) then "green"
          elif all($c[]; .done) then "untested"
          else "stale"
          end'
}

# at_night — true when this is not a scheduled run, or the UTC hour is inside the window.
at_night() {
    [ "${GITHUB_EVENT_NAME:-}" = schedule ] || return 0
    local h=$((10#$(date -u +%H)))
    [ "$h" -ge "$NIGHT_START_UTC" ] || [ "$h" -lt "$NIGHT_END_UTC" ]
}

merged_any=
git fetch --quiet origin main

# Separated by \x1f, not tabs: a tab is whitespace to `read`, so an empty field — a pull
# request with no labels, which is nearly all of them — collapsed into its neighbour and
# shifted the URL into $labels. dispatch-ci.sh was then handed an empty URL, GitHub
# refused the check run with a bare 422, and every rebased branch was left without CI.
prs=$(gh pr list --state open --base main --author app/github-actions --limit 100 \
    --json number,headRefName,createdAt,labels,url,isCrossRepository \
    --jq 'sort_by(.createdAt)[]
          | select(.headRefName | startswith("update/")) | select(.isCrossRepository | not)
          | [.number, .headRefName, ([.labels[].name] | join(",")), .url] | join("\u001f")')

# On fd 3, so that nothing in the loop body (gh, git) can read the list from stdin.
while IFS=$'\x1f' read -r -u 3 pr branch labels url; do
    [ -n "$pr" ] || continue
    if ! at_night; then
        echo "outside ${NIGHT_START_UTC}:00–0${NIGHT_END_UTC}:00 UTC, stopping; the rest waits for tomorrow night"
        break
    fi
    echo "#$pr $branch"

    if [[ ",$labels," == *",$HOLD_LABEL,"* ]]; then
        echo "  skip: labelled $HOLD_LABEL"; continue
    fi

    # One call, so the checks and the commit they are for cannot come from two moments.
    view=$(gh pr view "$pr" --json headRefOid,statusCheckRollup)
    seen=$(jq -r .headRefOid <<< "$view")
    state=$(checks_state <<< "$view")
    case "$state" in
        failed)  echo "  skip: a check failed, needs a human"; continue ;;
        pending) echo "  skip: a check is still running"; continue ;;
    esac

    git fetch --quiet origin main "+refs/heads/$branch:refs/remotes/origin/$branch"
    head=$(git rev-parse "origin/$branch")
    if [ "$head" != "$seen" ]; then
        echo "  branch moved since its checks were read, leaving it for the next run"
        continue
    fi

    if [ "$state" = green ] && git merge-base --is-ancestor origin/main "$head"; then
        echo "  green on top of the current main"
    else
        if git merge-base --is-ancestor origin/main "$head"; then
            echo "  up to date but checks are $state: building again"
        else
            echo "  behind main (checks: $state): rebasing and building again"
        fi
        if [ -n "$DRY_RUN" ]; then
            echo "  would rebase onto origin/main, push, run CI and merge if it passes"
            continue
        fi

        git switch --quiet -C "$branch" "$head"
        if ! git rebase --quiet origin/main; then
            git rebase --abort
            echo "::warning::#$pr does not rebase cleanly onto main, needs a human"
            continue
        fi
        if [ "$(git rev-parse HEAD)" != "$head" ]; then
            # The lease is the head this run looked at: if someone pushed a fix since,
            # this is refused rather than throwing their commit away.
            if ! git push --quiet --force-with-lease="$branch:$head" origin "HEAD:$branch"; then
                echo "::warning::#$pr changed while being rebased, leaving it for the next run"
                continue
            fi
            head=$(git rev-parse HEAD)
        fi

        if ! ./tools/dispatch-ci.sh "$branch" "$url"; then
            echo "  CI failed after the rebase, needs a human"
            continue
        fi
        # CI took a while; a fix pushed meanwhile is not the commit that was tested.
        git fetch --quiet origin "+refs/heads/$branch:refs/remotes/origin/$branch"
        if [ "$(git rev-parse "origin/$branch")" != "$head" ]; then
            echo "  branch moved while CI ran, leaving it for the next run"
            continue
        fi
    fi

    # Read again: a label or a close while it was built is a veto all the same.
    late=$(gh pr view "$pr" --json state,labels | jq -r '[.state, (.labels | map(.name) | join(","))] | join(" ")')
    if [ "${late%% *}" != OPEN ]; then
        echo "  ${late%% *} while it was built, leaving it" | tr '[:upper:]' '[:lower:]'; continue
    fi
    if [[ ",${late#* }," == *",$HOLD_LABEL,"* ]]; then
        echo "  labelled $HOLD_LABEL while it was built, leaving it"; continue
    fi

    # Not --force: a fast-forward or nothing, so main gets exactly the tested commit.
    if ! run git push --quiet origin "$head:refs/heads/main"; then
        echo "::warning::main moved during this run, #$pr waits for the next one"
        git fetch --quiet origin main
        continue
    fi
    echo "  merged $(git rev-parse --short "$head")"
    [ -n "$DRY_RUN" ] && continue
    merged_any=1
    git fetch --quiet origin main

    # GitHub marks the pull request merged once it sees its head on main, asynchronously.
    # The branch goes only after that: deleted first, the pull request closes unmerged.
    for _ in $(seq 12); do
        if [ "$(gh pr view "$pr" --json state --jq .state)" = MERGED ]; then
            git push --quiet origin --delete "$branch" || true
            break
        fi
        sleep 5
    done
done 3<<< "$prs"

if [ -n "$merged_any" ]; then
    gh workflow run ci.yml --ref main
    echo "dispatched ci.yml on main"
fi
