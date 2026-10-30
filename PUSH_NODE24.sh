#!/bin/sh
# Push the manifest/workflow updates. GitHub Actions mirrors the verified Node.js
# and OLLVM assets, then removes superseded releases only after publication passes.
set -eu

branch=$(git branch --show-current)
if [ "$branch" != "main" ]; then
  echo "Refusing to push from '$branch'; check out main and review the changes first." >&2
  exit 2
fi

if ! git diff --quiet || ! git diff --cached --quiet ||
   [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo "Refusing to push: commit all reviewed changes first." >&2
  exit 2
fi

git push origin HEAD:main
echo "Pushed main. Follow the Node.js 24.5.0 and OLLVM 23.1.1 mirror workflows."
echo "The workflow preserves the Codex releases still referenced by the manifest."
