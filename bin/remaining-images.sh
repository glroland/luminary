#!/usr/bin/env bash
#
# Remove from a CSV of files every file that already exists in another repo,
# and write the files still outstanding to an output CSV.
#
# Usage: bin/remaining-images.sh <input.csv> <repo-url> <output.csv> [--no-header] [--column N]
#   e.g. bin/remaining-images.sh files.csv smb://host/backup remaining.csv
#
# Column 1 of each input row (or column N with --column N) is the file path
# relative to the repo root, as written by bin/list-images.sh. A row whose path
# exists in <repo-url> is dropped; every other row is written to the output
# unchanged (all its columns), in its original order. Existence is an exact,
# case-sensitive path match against a single recursive listing of <repo-url>
# (one listing, not one lookup per row). Only presence is checked, not size or
# content. The first line is assumed to be a header: it is copied to the output
# as is; pass --no-header if the file has none. Blank lines and lines starting
# with '#' are skipped. Values may be double-quoted; the output quotes every
# value. Paths that appear on more than one row are all kept or all dropped.
#
# Read-only: it only lists <repo-url>, never writes to it. The repo may be
# anything fsspec understands (local path, smb://, s3://, https://, ...).
# Credentials for smb:// come from LUMINARY_PHOTOS_USERNAME /
# LUMINARY_PHOTOS_PASSWORD (never put them in the URL). Uses the project
# venv's Python (target/venv; run `make install`).
#
# Exit codes: 0 done, 1 failed, 2 bad usage.

set -uo pipefail

HEADER=1
COLUMN=1
ARGS=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-header) HEADER=0 ;;
        --column)
            shift
            case "${1:-}" in
                ''|*[!0-9]*|0) echo "error: --column needs a positive number" >&2; exit 2 ;;
            esac
            COLUMN="$1" ;;
        -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        --*) echo "unknown option: $1" >&2; exit 2 ;;
        *) ARGS+=("$1") ;;
    esac
    shift
done
if [ "${#ARGS[@]}" -ne 3 ]; then
    echo "usage: $0 <input.csv> <repo-url> <output.csv> [--no-header] [--column N]" >&2
    exit 2
fi
export REMAINING_HEADER="$HEADER" REMAINING_COLUMN="$COLUMN"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${LUMINARY_PYTHON:-$ROOT/target/venv/bin/python}"

if [ ! -x "$PYTHON" ]; then
    echo "error: $PYTHON not found; run 'make install' first" >&2
    exit 1
fi

exec "$PYTHON" -I - "${ARGS[@]}" <<'PY'
import csv
import os
import sys

import fsspec

USERNAME_PASSWORD_PROTOCOLS = {"smb"}


def existing_paths(url):
    kwargs = {}
    protocol = url.split("://", 1)[0] if "://" in url else "file"
    user = os.environ.get("LUMINARY_PHOTOS_USERNAME")
    password = os.environ.get("LUMINARY_PHOTOS_PASSWORD")
    if protocol in USERNAME_PASSWORD_PROTOCOLS and user:
        kwargs["username"] = user
        kwargs["password"] = password or ""
    fs, root = fsspec.core.url_to_fs(url, **kwargs)
    root = root.rstrip("/")
    prefix = root + "/"
    return {
        p[len(prefix):] if p.startswith(prefix) else p.lstrip("/")
        for p in fs.find(root)
    }


def main(in_path, url, out_path):
    has_header = os.environ["REMAINING_HEADER"] == "1"
    col = int(os.environ["REMAINING_COLUMN"]) - 1

    try:
        have = existing_paths(url)
    except Exception as exc:
        print(f"error: could not list {url}: {exc}", file=sys.stderr)
        return 1

    try:
        with open(in_path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    except OSError as exc:
        print(f"error: could not read {in_path}: {exc}", file=sys.stderr)
        return 1

    header = rows.pop(0) if has_header and rows else None
    kept = dropped = 0
    try:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, quoting=csv.QUOTE_ALL)
            if header is not None:
                w.writerow(header)
            for row in rows:
                if not row or all(not c.strip() for c in row):
                    continue
                if row[0].lstrip().startswith("#"):
                    continue
                path = row[col].strip().strip("/") if col < len(row) else ""
                if path and path in have:
                    dropped += 1
                    continue
                w.writerow(row)
                kept += 1
    except OSError as exc:
        print(f"error: could not write {out_path}: {exc}", file=sys.stderr)
        return 1

    print(f"{dropped} already in {url}; wrote {kept} remaining to {out_path}")
    return 0


sys.exit(main(*sys.argv[1:4]))
PY
