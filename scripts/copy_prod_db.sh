#!/bin/bash
# scripts/copy_prod_db.sh
#
# Safely copies the production database to the dev data directory.
# Uses SQLite .backup for safe copy (handles WAL mode).

PROD_DB="/Volumes/externalSSD/media/docker/pagekeeper/data/database.db"
DEV_DATA="$(cd "$(dirname "$0")/.." && pwd)/data"
DEV_DB="${DEV_DATA}/database.db"

# Check prod database exists
if [ ! -f "$PROD_DB" ]; then
    echo "Error: Production database not found at $PROD_DB"
    exit 1
fi

# Create dev data dir if needed
mkdir -p "$DEV_DATA"

# Warn if dev DB already exists
if [ -f "$DEV_DB" ]; then
    echo "Dev database already exists at $DEV_DB"
    read -p "Overwrite? [y/N] " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Aborted."
        exit 0
    fi
fi

# Safe copy using SQLite .backup command
echo "Copying production database to dev..."
sqlite3 "$PROD_DB" ".backup '$DEV_DB'"

if [ $? -eq 0 ]; then
    echo "Done. Dev database updated at $DEV_DB"
else
    echo "Error: SQLite backup failed."
    exit 1
fi
