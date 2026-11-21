#!/usr/bin/env bash
#
# Move every image listed in a CSV from one repo to another, by calling
# bin/move-image.sh once per row.
#
# Usage: bin/move-images.sh <file-list.csv> <source-repo-url> <target-repo-url> [--continue-on-error] [--no-header] [--force] [--dry-run] [--column N] [--debug]
#   e.g. bin/move-images.sh files.csv smb://host/images smb://host/backup
#
# Column 1 of each row (or column N with --column N) is the image path relative
# to the repo root; any other columns are ignored. The app's duplicates CSV puts
# the duplicate in column 1 and its original in column 2; for an older download
# with the columns the other way round, use --column 2. A path that appears on
# more than one row (an original with several duplicates) is moved once; later
# rows for it are skipped. Values may be wrapped in double quotes (the CSVs the
# app downloads quote every value); inside quotes a comma is literal and ""
# is an escaped quote. The first line of the file is assumed to be a header
# and is skipped; pass --no-header if the file has none. Blank lines and lines
# starting with '#' are skipped. #
# By default the first failed move stops the run (exit 1). With
# --continue-on-error it carries on with the remaining rows, prints a summary
# of the failures, and exits 1 at the end if any row failed.
#
# --force is passed through to move-image.sh: a target that already exists is
# replaced (after the new copy is verified) instead of failing the row. Use it
# to finish moves whose earlier run copied a file but could not delete the source.
#
# --dry-run is passed through to move-image.sh: every row is checked (source
# exists, target free or --force) and reported as "would move", but nothing is
# copied or deleted. Failures are reported as usual, so it shows which rows a
# real run would fail on.
#
# --debug prints each step and its result to stdout (this script's row
# handling, plus move-image.sh's per-file steps, which it is passed through to).
#
# Exit codes: 0 all moved, 1 one or more moves failed, 2 bad usage.
# See move-image.sh for the per-file copy-verify-delete semantics and
# credentials (LUMINARY_PHOTOS_USERNAME / LUMINARY_PHOTOS_PASSWORD).

set -uo pipefail

usage() {
    echo "usage: $0 <file-list.csv> <source-repo-url> <target-repo-url> [--continue-on-error] [--no-header] [--force] [--dry-run] [--column N] [--debug]" >&2
    exit 2
}

dbg() { if [ "$DEBUG" -eq 1 ]; then echo "[debug] $*"; fi; }

# Print column N (1-based) of one CSV line, with surrounding double quotes
# removed. Handles quoted fields containing commas and "" escapes; unquoted
# fields are fine too. Prints nothing if the line has fewer than N columns.
nth_column() {
    local line="$1" want="$2" col=1 out="" inq=0 i ch next
    for ((i = 0; i < ${#line}; i++)); do
        ch="${line:i:1}"
        if [ "$inq" -eq 1 ]; then
            if [ "$ch" = '"' ]; then
                next="${line:i+1:1}"
                if [ "$next" = '"' ]; then
                    [ "$col" -eq "$want" ] && out+='"'
                    i=$((i + 1))
                else
                    inq=0
                fi
            elif [ "$col" -eq "$want" ]; then
                out+="$ch"
            fi
        elif [ "$ch" = '"' ]; then
            inq=1
        elif [ "$ch" = ',' ]; then
            [ "$col" -eq "$want" ] && break
            col=$((col + 1))
        elif [ "$col" -eq "$want" ]; then
            out+="$ch"
        fi
    done
    [ "$col" -eq "$want" ] && printf '%s' "$out"
}

CONTINUE=0
COLUMN=1
HEADER=1
DEBUG=0
FORCE=0
DRY_RUN=0
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --debug) DEBUG=1 ;;
        --continue-on-error) CONTINUE=1 ;;
        --no-header) HEADER=0 ;;
        --force) FORCE=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --column)
            shift
            case "${1:-}" in ''|*[!0-9]*|0) echo "error: --column needs a positive number" >&2; exit 2 ;; esac
            COLUMN="$1" ;;
        -h|--help) usage ;;
        *) ARGS+=("$1") ;;
    esac
    shift
done
[ "${#ARGS[@]}" -eq 3 ] || usage

CSV="${ARGS[0]}"
SOURCE="${ARGS[1]}"
TARGET="${ARGS[2]}"
MOVE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/move-image.sh"

if [ ! -r "$CSV" ]; then
    echo "error: cannot read $CSV" >&2
    exit 2
fi

MOVE_ARGS=()
[ "$DEBUG" -eq 1 ] && MOVE_ARGS+=(--debug)
[ "$FORCE" -eq 1 ] && MOVE_ARGS+=(--force)
[ "$DRY_RUN" -eq 1 ] && MOVE_ARGS+=(--dry-run)

dbg "csv=$CSV source=$SOURCE target=$TARGET header=$HEADER continue-on-error=$CONTINUE"
dbg "per-file script: $MOVE"

SEEN="$(mktemp)"
trap 'rm -f "$SEEN"' EXIT
moved=0
skipped=0
failed=0
failures=()
lineno=0

# `|| [ -n "$line" ]` handles a last line with no trailing newline.
# stdin of move-image.sh is /dev/null so it can never swallow the CSV.
while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))
    if [ "$lineno" -eq 1 ] && [ "$HEADER" -eq 1 ]; then
        dbg "line $lineno: skipping header: $line"
        continue
    fi
    line="${line%$'\r'}"
    case "$line" in
        '') dbg "line $lineno: skipping blank line"; continue ;;
        '#'*) dbg "line $lineno: skipping comment"; continue ;;
    esac

    path="$(nth_column "$line" "$COLUMN")"
    [ -n "$path" ] || { dbg "line $lineno: empty column $COLUMN, skipping"; continue; }
    if grep -Fxq -- "$path" "$SEEN"; then
        dbg "line $lineno: '$path' already handled on an earlier row, skipping"
        skipped=$((skipped + 1))
        continue
    fi
    printf '%s\n' "$path" >> "$SEEN"

    dbg "line $lineno: moving '$path'"
    if "$MOVE" "$SOURCE" "$TARGET" "$path" ${MOVE_ARGS[@]+"${MOVE_ARGS[@]}"} </dev/null; then
        dbg "line $lineno: result: success"
        moved=$((moved + 1))
    else
        dbg "line $lineno: result: FAILED (exit $?)"
        failed=$((failed + 1))
        failures+=("$path")
        if [ "$CONTINUE" -eq 0 ]; then
            echo "stopping after first failure: $path (use --continue-on-error to keep going)" >&2
            echo "moved: $moved, failed: $failed" >&2
            exit 1
        fi
    fi
done < "$CSV"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry run: would move: $moved, would fail: $failed, repeated paths skipped: $skipped"
else
    echo "moved: $moved, failed: $failed, repeated paths skipped: $skipped"
fi
if [ "$failed" -gt 0 ]; then
    echo "failed files:" >&2
    printf '  %s\n' "${failures[@]}" >&2
    exit 1
fi
exit 0
