#!/usr/bin/env bash
# Copy the contents of SRC_DIR onto the results branch and push.
#   publish.sh SRC_DIR "commit message"
# The website reads results from this branch, so main stays clean. Retries
# with a rebase because several searches can finish at the same moment.
set -euo pipefail
src="$1"; msg="$2"
branch="${RESULTS_BRANCH:-blast-results}"
url="https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
dir="$(mktemp -d)"

if git ls-remote --exit-code --heads "$url" "$branch" >/dev/null 2>&1; then
  git clone -q --depth 1 --branch "$branch" "$url" "$dir"
else
  git init -q "$dir"
  git -C "$dir" checkout -q --orphan "$branch"
  git -C "$dir" remote add origin "$url"
  printf '# BLAST results\n\nWritten automatically by the Run BLAST workflow. The website reads these files.\n' > "$dir/README.md"
fi

cp -R "$src"/. "$dir"/
cd "$dir"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
if git diff --cached --quiet; then
  echo "Nothing new to publish"; exit 0
fi
git commit -q -m "$msg"
for attempt in 1 2 3 4 5 6; do
  if git push -q origin "HEAD:$branch"; then echo "Published to $branch"; exit 0; fi
  sleep $((attempt * 3))
  git pull -q --rebase origin "$branch"
done
echo "::error::Could not push to $branch"; exit 1
