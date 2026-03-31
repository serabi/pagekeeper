#!/bin/sh

set -eu

repo_root="$(git rev-parse --show-toplevel)"
config_file="$repo_root/config/private-paths.txt"
mode="index"
ref=""

usage() {
    echo "Usage: scripts/git/verify-public-tree.sh [--ref <ref>]"
}

if [ ! -f "$config_file" ]; then
    echo "Missing private path config: $config_file" >&2
    exit 1
fi

while [ $# -gt 0 ]; do
    case "$1" in
        --ref)
            ref="${2-}"
            [ -n "$ref" ] || {
                usage >&2
                exit 1
            }
            mode="ref"
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

if [ "$mode" = "ref" ]; then
    git rev-parse --verify "$ref^{commit}" >/dev/null 2>&1 || {
        echo "Unknown ref: $ref" >&2
        exit 1
    }
fi

violations=0

while IFS= read -r path || [ -n "$path" ]; do
    case "$path" in
        ""|\#*)
            continue
            ;;
    esac

    if [ "$mode" = "ref" ]; then
        match="$(git ls-tree -r --name-only "$ref" -- "$path")"
        if [ -n "$match" ]; then
            if [ "$violations" -eq 0 ]; then
                echo "Public branch contains private-only paths:" >&2
            fi
            printf '  %s\n' "$path" >&2
            violations=1
        fi
        continue
    fi

    match="$(git ls-files --cached -- "$path")"
    if [ -n "$match" ]; then
        if [ "$violations" -eq 0 ]; then
            echo "Current index contains private-only paths:" >&2
        fi
        printf '  %s\n' "$path" >&2
        violations=1
    fi
done < "$config_file"

if [ "$violations" -ne 0 ]; then
    exit 1
fi

if [ "$mode" = "ref" ]; then
    echo "Verified public tree for $ref"
else
    echo "Verified current index for public promotion"
fi
