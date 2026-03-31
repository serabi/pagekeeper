#!/bin/sh

set -eu

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

git config core.hooksPath .githooks

chmod +x .githooks/pre-push scripts/git/*.sh

echo "Configured core.hooksPath=.githooks"
echo "Executable permissions refreshed for scripts/git and .githooks/pre-push"
