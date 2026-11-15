#!/usr/bin/env bash
# Publish the app from NEW runs of deploy-app.yml (owner 6 Nov 2026). Pages ties a publish to a commit id: a hung / cancelled publish of one
# commit can never be repeated, so every try needs a commit id that was never published. Attempt 1 uses the newest commit (this run's post);
# attempt 2 first pushes an empty commit. Exit 0 = published, 1 = both failed.
for n in 1 2; do
  echo "fallback publish attempt $n"
  if [ "$n" -gt 1 ]; then
    git config user.name "news-bot"
    git config user.email "news-bot@users.noreply.github.com"
    git commit --allow-empty -q -m "Republish app (attempt $n)" \
      && git pull -q --rebase origin main \
      && git push -q origin HEAD:main \
      || echo "(empty commit not pushed)"
  fi
  gh workflow run deploy-app.yml --ref main || continue
  sleep 20
  id=$(gh run list --workflow=deploy-app.yml --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId')
  echo "watching run $id"
  if timeout 420 gh run watch "$id" --interval 10 --exit-status; then
    echo "published by run $id"
    exit 0
  fi
done
echo "::error::Pages publish failed after the fallback runs"
exit 1
