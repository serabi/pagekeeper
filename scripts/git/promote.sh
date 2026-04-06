#!/bin/sh

set -eu

repo_root="$(git rev-parse --show-toplevel)"
verify_script="$repo_root/scripts/git/verify-public-tree.sh"
config_file="$repo_root/config/private-paths.txt"
push_after=0

usage() {
    cat <<'EOF'
Usage:
  scripts/git/promote.sh [--push] <source-branch> <dest-branch> [commit message]

Examples:
  scripts/git/promote.sh feature/my-change dev "feat: publish feature snapshot"
  scripts/git/promote.sh --push dev main "release: v0.2.0"
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

src="${1-}"
dst="${2-}"
msg="${3-}"

[ -n "$src" ] && [ -n "$dst" ] || {
    usage >&2
    exit 1
}

[ "$src" != "$dst" ] || {
    echo "Source and destination branches must differ." >&2
    exit 1
}

if [ ! -f "$config_file" ]; then
    echo "Missing private path config: $config_file" >&2
    exit 1
fi

cd "$repo_root"

if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
    echo "Working tree must be completely clean before promotion." >&2
    exit 1
fi

git rev-parse --verify "$src^{commit}" >/dev/null 2>&1 || {
    echo "Unknown source branch: $src" >&2
    exit 1
}

git rev-parse --verify "$dst^{commit}" >/dev/null 2>&1 || {
    echo "Unknown destination branch: $dst" >&2
    exit 1
}

git checkout "$dst" >/dev/null 2>&1

upstream="$(git for-each-ref --format='%(upstream:short)' "refs/heads/$dst")"
if [ -n "$upstream" ]; then
    remote="${upstream%%/*}"
    remote_branch="${upstream#*/}"
    git fetch "$remote" "$remote_branch" >/dev/null 2>&1
    git merge --ff-only "$upstream" >/dev/null 2>&1
fi

source_sha="$(git rev-parse "$src^{commit}")"

# Replace the destination branch contents with an exact snapshot of the source
# branch before stripping private-only paths.
git read-tree --reset -u "$src"

while IFS= read -r path || [ -n "$path" ]; do
    case "$path" in
        ""|\#*)
            continue
            ;;
    esac
    git rm -r -f --ignore-unmatch --quiet -- "$path" >/dev/null 2>&1 || true
done < "$config_file"

"$verify_script"

if git diff --cached --quiet; then
    echo "Nothing to commit after promotion."
    exit 0
fi

if [ -z "$msg" ]; then
    msg="Promote $src -> $dst"
fi

git commit -m "$msg" -m "Promoted-From: $src@$source_sha"

if [ "$push_after" -eq 1 ]; then
    push_remote="$(git config "branch.$dst.remote" || true)"
    if [ -z "$push_remote" ]; then
        echo "Destination branch '$dst' has no configured remote." >&2
        exit 1
    fi
    git push "$push_remote" "$dst"
fi
