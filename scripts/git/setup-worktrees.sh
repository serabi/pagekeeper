#!/bin/sh

set -eu

repo_root="$(git rev-parse --show-toplevel)"
parent_dir="${1-$(dirname "$repo_root")}"
repo_name="$(basename "$repo_root")"

cd "$repo_root"

if ! git show-ref --verify --quiet refs/heads/draft && \
   git show-ref --verify --quiet refs/remotes/private/draft; then
    git branch --track draft private/draft >/dev/null 2>&1 || true
fi

active_branches="$(git worktree list --porcelain | awk '/^branch / {sub("^refs/heads/","",$2); print $2}')"

add_worktree() {
    branch="$1"
    target_dir="$2"

    git show-ref --verify --quiet "refs/heads/$branch" || {
        echo "Skipping $branch: local branch does not exist."
        return
    }

    if printf '%s\n' "$active_branches" | grep -qx "$branch"; then
        echo "Skipping $branch: already checked out in another worktree."
        return
    fi

    if [ -e "$target_dir" ]; then
        echo "Skipping $branch: target path already exists: $target_dir"
        return
    fi

    git worktree add "$target_dir" "$branch"
}

add_worktree draft "$parent_dir/$repo_name-draft"
add_worktree main "$parent_dir/$repo_name-main"
