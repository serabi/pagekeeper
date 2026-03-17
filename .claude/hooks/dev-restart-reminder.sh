#!/usr/bin/env bash
# Claude Code PostToolUse hook: reminds to restart dev container after source file edits

set -euo pipefail

INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || echo "")
FILE_PATH=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null || echo "")

# Only care about Edit and Write tools
if [ "$TOOL" != "Edit" ] && [ "$TOOL" != "Write" ]; then
    exit 0
fi

# Check if the edited file is in a source directory
if echo "$FILE_PATH" | grep -qE '/(src|templates|static|alembic|scripts)/'; then
    echo "Restart the dev container to pick up changes: docker compose -f docker-compose.dev.yml restart"
fi

exit 0
