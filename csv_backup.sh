#!/bin/bash
# Month-end housekeeping for ~/temp/data_collection/csv.
# Archives files not modified since the start of this week into a tar.gz, then
# deletes only the files that made it into the verified archive.
# Never touched: Commodity-*, IndexFuture-* and the rolling files
# (ledger_tracking.csv, fund_snapshots.csv, processed_files.json).
# Age is taken from mtime, not from the date in the filename: nifty_pos_* files
# are named after their expiry date, so the name says nothing about whether the
# engine is still writing to the file.
# Run with --dry-run to list what would be archived.

CSV_DIR=${CSV_DIR:-/home/pitest/temp/data_collection/csv}
BACKUP_DIR=${BACKUP_DIR:-/home/pitest/temp/csv_backups}

DRY_RUN=0
[ "$1" = "--dry-run" ] && DRY_RUN=1

cd "$CSV_DIR" || { echo "no such dir: $CSV_DIR"; exit 1; }
mkdir -p "$BACKUP_DIR" || exit 1

# Monday of the current week; files dated on or after this are kept
dow=$(date +%u)
week_start=$(date -d "-$((dow - 1)) days" +%Y-%m-%d)

list=$(mktemp)
find . -maxdepth 1 -type f ! -newermt "$week_start 00:00" \
    ! -name 'Commodity-*' \
    ! -name 'IndexFuture-*' \
    ! -name 'ledger_tracking.csv' \
    ! -name 'fund_snapshots.csv' \
    ! -name 'processed_files.json' \
    -printf '%P\n' | sort > "$list"

count=$(wc -l < "$list")
if [ "$count" -eq 0 ]; then
    echo "$(date '+%F %T') nothing untouched since $week_start, nothing to do"
    rm -f "$list"
    exit 0
fi

archive="$BACKUP_DIR/csv_backup_$(date +%Y-%m).tar.gz"
[ -e "$archive" ] && archive="$BACKUP_DIR/csv_backup_$(date +%Y-%m-%d_%H%M%S).tar.gz"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "would archive $count files untouched since $week_start into $archive"
    cat "$list"
    rm -f "$list"
    exit 0
fi

if ! tar -czf "$archive" -T "$list"; then
    echo "$(date '+%F %T') tar failed, nothing deleted"
    rm -f "$list"
    exit 1
fi

if ! tar -tzf "$archive" > /dev/null; then
    echo "$(date '+%F %T') archive verify failed, nothing deleted: $archive"
    rm -f "$list"
    exit 1
fi

xargs -a "$list" -d '\n' rm -f
echo "$(date '+%F %T') archived $count files into $archive, kept everything modified since $week_start"
rm -f "$list"
