#!/bin/sh

set -eu

repo_root="$(git rev-parse --show-toplevel)"
force=0
target="${1-}"

usage() {
    cat <<'EOF'
Usage:
  scripts/git/sync-private-mirrors.sh dev
  scripts/git/sync-private-mirrors.sh main
  scripts/git/sync-private-mirrors.sh --all
  scripts/git/sync-private-mirrors.sh --force --all
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --all)
            target="all"
            shift
            ;;
        --force)
            force=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        dev|main)
            target="$1"
            shift
            ;;
        *)
            usage >&2
            exit 1
            ;;
    esac
done

[ -n "$target" ] || {
    usage >&2
    exit 1
}

cd "$repo_root"

git remote get-url origin >/dev/null 2>&1 || {
    echo "Missing origin remote." >&2
    exit 1
}

git remote get-url private >/dev/null 2>&1 || {
    echo "Missing private remote." >&2
    exit 1
}

git fetch origin --prune >/dev/null 2>&1
git fetch private --prune >/dev/null 2>&1

sync_branch() {
    branch="$1"
    origin_ref="refs/remotes/origin/$branch"
    private_ref="refs/remotes/private/$branch"

    git rev-parse --verify "$origin_ref^{commit}" >/dev/null 2>&1 || {
        echo "Missing origin branch: $branch" >&2
        exit 1
    }

    origin_sha="$(git rev-parse "$origin_ref^{commit}")"

    if git show-ref --verify --quiet "$private_ref"; then
        private_sha="$(git rev-parse "$private_ref^{commit}")"
        if [ "$origin_sha" = "$private_sha" ]; then
            echo "private/$branch already matches origin/$branch"
            return
        fi
    fi

    if [ "$force" -eq 1 ]; then
        git push private --force-with-lease="refs/heads/$branch" \
            "$origin_sha:refs/heads/$branch"
    else
        git push private "$origin_sha:refs/heads/$branch"
    fi
}

if [ "$target" = "all" ]; then
    sync_branch dev
    sync_branch main
    exit 0
fi

sync_branch "$target"
