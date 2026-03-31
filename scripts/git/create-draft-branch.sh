#!/bin/sh

set -eu

repo_root="$(git rev-parse --show-toplevel)"
archive_legacy=1
archive_name=""

usage() {
    cat <<'EOF'
Usage:
  scripts/git/create-draft-branch.sh [--no-archive] [--archive-name <name>]

Creates private/draft from private/dev and optionally archives private/dev-private.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --no-archive)
            archive_legacy=0
            shift
            ;;
        --archive-name)
            archive_name="${2-}"
            [ -n "$archive_name" ] || {
                usage >&2
                exit 1
            }
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 1
            ;;
    esac
done

cd "$repo_root"

git remote get-url private >/dev/null 2>&1 || {
    echo "Missing private remote." >&2
    exit 1
}

git fetch private --prune >/dev/null 2>&1

git rev-parse --verify refs/remotes/private/dev^{commit} >/dev/null 2>&1 || {
    echo "Missing private/dev. Sync the private mirror first." >&2
    exit 1
}

if git show-ref --verify --quiet refs/remotes/private/draft; then
    echo "private/draft already exists."
    exit 0
fi

draft_sha="$(git rev-parse refs/remotes/private/dev^{commit})"
git push private "$draft_sha:refs/heads/draft"
echo "Created private/draft from private/dev at $draft_sha"

if [ "$archive_legacy" -eq 1 ] && \
   git show-ref --verify --quiet refs/remotes/private/dev-private; then
    if [ -z "$archive_name" ]; then
        archive_name="archive/dev-private-$(date +%Y-%m-%d)"
    fi
    legacy_sha="$(git rev-parse refs/remotes/private/dev-private^{commit})"
    git push private "$legacy_sha:refs/heads/$archive_name"
    echo "Archived private/dev-private to private/$archive_name"
fi

echo "Next step: git branch --track draft private/draft"
