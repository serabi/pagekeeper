#!/bin/sh

set -eu

repo_root="$(git rev-parse --show-toplevel)"
verify_script="$repo_root/scripts/git/verify-public-tree.sh"
config_file="$repo_root/config/private-paths.txt"
push_after=0

usage() {
    cat <<'EOF'
Usage:
  scripts/git/sanitize-public-branch.sh [--push] <branch> [commit message]

Examples:
  scripts/git/sanitize-public-branch.sh dev "chore: strip private-only files from dev"
  scripts/git/sanitize-public-branch.sh --push main "chore: strip private-only files from main"
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --push)
            push_after=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            break
            ;;
    esac
done

branch="${1-}"
msg="${2-}"

[ -n "$branch" ] || {
    usage >&2
    exit 1
}

case "$branch" in
    dev|main)
        ;;
    *)
        echo "Sanitize only supports public branches: dev or main." >&2
        exit 1
        ;;
esac

[ -f "$config_file" ] || {
    echo "Missing private path config: $config_file" >&2
    exit 1
}

cd "$repo_root"

if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
    echo "Working tree must be completely clean before sanitizing." >&2
    exit 1
fi

git rev-parse --verify "$branch^{commit}" >/dev/null 2>&1 || {
    echo "Unknown branch: $branch" >&2
    exit 1
}

git checkout "$branch" >/dev/null 2>&1

upstream="$(git for-each-ref --format='%(upstream:short)' "refs/heads/$branch")"
if [ -n "$upstream" ]; then
    remote="${upstream%%/*}"
    remote_branch="${upstream#*/}"
    git fetch "$remote" "$remote_branch" >/dev/null 2>&1
    git merge --ff-only "$upstream" >/dev/null 2>&1
fi

while IFS= read -r path || [ -n "$path" ]; do
    case "$path" in
        ""|\#*)
            continue
            ;;
    esac
    git rm -r -f --ignore-unmatch --quiet -- "$path" >/dev/null 2>&1 || true
    rm -rf -- "$path" >/dev/null 2>&1 || true
done < "$config_file"

"$verify_script"

if git diff --cached --quiet; then
    echo "Nothing to commit after sanitizing $branch."
    exit 0
fi

if [ -z "$msg" ]; then
    msg="Sanitize $branch by stripping private-only paths"
fi

git commit -m "$msg"

if [ "$push_after" -eq 1 ]; then
    push_remote="$(git config "branch.$branch.remote" || true)"
    if [ -z "$push_remote" ]; then
        echo "Branch '$branch' has no configured remote." >&2
        exit 1
    fi
    git push "$push_remote" "$branch"
fi
