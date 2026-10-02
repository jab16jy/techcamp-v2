#!/usr/bin/env bash
# What a PR says about itself (#234). The Conventional Commit title is a hard failure: it
# becomes the squash commit and the changelog line. The labels and the issue reference
# only warn, because most merged PRs never carried them and a check that is red on nearly
# every PR teaches people to ignore it. The warnings show as annotations and in the job
# summary. Values arrive through the environment, never interpolated into this script.
set -euo pipefail

title=${PR_TITLE:?PR_TITLE is required}
body=${PR_BODY:-}
labels=$(jq -r '.[]' <<<"${PR_LABELS_JSON:-[]}")
summary=${GITHUB_STEP_SUMMARY:-/dev/null}

# A scope may name several modules: fix(irrigation,farms,telemetry): ...
title_re='^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([a-z0-9._/,-]+\))?!?: .+'

warn() {
  echo "::warning title=PR validation::$1"
  echo "- warning: $1" >>"$summary"
}

failed=0
if ! [[ $title =~ $title_re ]]; then
  echo "::error title=PR validation::the title is not a Conventional Commit: <type>(<scope>): <summary>"
  echo "- error: the title '$title' is not a Conventional Commit" >>"$summary"
  failed=1
fi
grep -q '^epic:' <<<"$labels" || warn "no epic:* label"
grep -q '^type:' <<<"$labels" || warn "no type:* label"
grep -Eiq '(refs|closes|fixes|resolves) #[0-9]+' <<<"$body" || warn "the body has no Refs/Closes #N"

exit "$failed"
