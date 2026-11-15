#!/usr/bin/env bash
#
# Move every image listed in a CSV from one repo to another, by calling
# bin/move-image.sh once per row.
#
# Usage: bin/move-images.sh <file-list.csv> <source-repo-url> <target-repo-url> [--continue-on-error] [--no-header] [--debug]
#   e.g. bin/move-images.sh files.csv smb://host/images smb://host/backup
#
# Column 1 of each row is the image path relative to the repo root; any other
# columns are ignored. The first line of the file is assumed to be a header
# and is skipped; pass --no-header if the file has none. Blank lines and lines
# starting with '#' are skipped. Column 1 may be wrapped in double quotes, but
# a path containing a comma is not supported.
#
# By default the first failed move stops the run (exit 1). With
# --continue-on-error it carries on with the remaining rows, prints a summary
# of the failures, and exits 1 at the end if any row failed.
#
# --debug prints each step and its result to stdout (this script's row
# handling, plus move-image.sh's per-file steps, which it is passed through to).
#
# Exit codes: 0 all moved, 1 one or more moves failed, 2 bad usage.
# See move-image.sh for the per-file copy-verify-delete semantics and
# credentials (LUMINARY_PHOTOS_USERNAME / LUMINARY_PHOTOS_PASSWORD).

set -uo pipefail

usage() {
    echo "usage: $0 <file-list.csv> <source-repo-url> <target-repo-url> [--continue-on-error] [--no-header] [--debug]" >&2
    exit 2
}

dbg() { if [ "$DEBUG" -eq 1 ]; then echo "[debug] $*"; fi; }

CONTINUE=0
HEADER=1
DEBUG=0
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --debug) DEBUG=1 ;;
        --continue-on-error) CONTINUE=1 ;;
        --no-header) HEADER=0 ;;
        -h|--help) usage ;;
        *) ARGS+=("$arg") ;;
    esac
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

dbg "csv=$CSV source=$SOURCE target=$TARGET header=$HEADER continue-on-error=$CONTINUE"
dbg "per-file script: $MOVE"

moved=0
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

    path="${line%%,*}"
    path="${path#\"}"
    path="${path%\"}"
    [ -n "$path" ] || { dbg "line $lineno: empty first column, skipping"; continue; }

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

echo "moved: $moved, failed: $failed"
if [ "$failed" -gt 0 ]; then
    echo "failed files:" >&2
    printf '  %s\n' "${failures[@]}" >&2
    exit 1
fi
exit 0
