#!/usr/bin/env bash
#
# Delete every file listed in a CSV from an image store.
#
# Usage: bin/delete-images.sh <file-list.csv> <repo-url> [--yes] [--dry-run] [--no-header] [--column N]
#   e.g. bin/delete-images.sh dups.csv smb://host/photo
#
# Column 1 of each row (or column N with --column N) is the file's path
# relative to the repo root; other columns are ignored. Values may be
# double-quoted. The first line is assumed to be a header and is skipped
# (--no-header if there is none). Blank lines and lines starting with '#' are
# skipped, and a path listed more than once is handled once.
#
# Each file is confirmed on the terminal before it is deleted: y = delete,
# n/Enter = skip, a = delete this and all remaining without asking, q = quit.
# --yes skips the prompts and deletes everything (use with care).
# --dry-run only reports what would be deleted.
#
# This DELETES FILES FROM THE STORE and cannot be undone. Only single files are
# removed, never directories; paths that are absolute or contain '..' are
# refused; a missing file is reported and skipped. The Luminary API itself
# never deletes from the photos backend -- this is a separate, manual tool.
#
# The repo may be anything fsspec understands (local path, smb://, s3://, ...).
# Credentials for smb:// come from LUMINARY_PHOTOS_USERNAME /
# LUMINARY_PHOTOS_PASSWORD. Uses the project venv's Python (run `make install`).
#
# Exit codes: 0 done, 1 one or more deletions failed, 2 bad usage.

set -uo pipefail

HEADER=1
YES=0
DRY=0
COLUMN=1
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --yes) YES=1 ;;
        --dry-run) DRY=1 ;;
        --no-header) HEADER=0 ;;
        --column)
            shift
            case "${1:-}" in
                ''|*[!0-9]*|0) echo "--column needs a positive number" >&2; exit 2 ;;
            esac
            COLUMN="$1" ;;
        -h|--help) sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        --*) echo "unknown option: $1" >&2; exit 2 ;;
        *) ARGS+=("$1") ;;
    esac
    shift
done
if [ "${#ARGS[@]}" -ne 2 ]; then
    echo "usage: $0 <file-list.csv> <repo-url> [--yes] [--dry-run] [--no-header] [--column N]" >&2
    exit 2
fi
export DELETE_IMAGES_HEADER="$HEADER" DELETE_IMAGES_YES="$YES" \
       DELETE_IMAGES_DRY="$DRY" DELETE_IMAGES_COLUMN="$COLUMN"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${LUMINARY_PYTHON:-$ROOT/target/venv/bin/python}"

if [ ! -x "$PYTHON" ]; then
    echo "error: $PYTHON not found; run 'make install' first" >&2
    exit 2
fi
if [ ! -r "${ARGS[0]}" ]; then
    echo "error: cannot read ${ARGS[0]}" >&2
    exit 2
fi

exec "$PYTHON" -I - "${ARGS[@]}" <<'PY'
import csv
import os
import sys

import fsspec

USERNAME_PASSWORD_PROTOCOLS = {"smb"}
HEADER = os.environ["DELETE_IMAGES_HEADER"] == "1"
YES = os.environ["DELETE_IMAGES_YES"] == "1"
DRY = os.environ["DELETE_IMAGES_DRY"] == "1"
COLUMN = int(os.environ["DELETE_IMAGES_COLUMN"])


def read_paths(csv_path):
    paths, seen = [], set()
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    for i, row in enumerate(csv.reader(lines)):
        if i == 0 and HEADER:
            continue
        if len(row) < COLUMN or not row[COLUMN - 1].strip():
            continue
        p = row[COLUMN - 1].strip()
        if p not in seen:
            seen.add(p)
            paths.append(p)
    return paths


def unsafe(p):
    return p.startswith("/") or ".." in p.replace("\\", "/").split("/")


def main(csv_path, url):
    kwargs = {}
    protocol = url.split("://", 1)[0] if "://" in url else "file"
    user = os.environ.get("LUMINARY_PHOTOS_USERNAME")
    password = os.environ.get("LUMINARY_PHOTOS_PASSWORD")
    if protocol in USERNAME_PASSWORD_PROTOCOLS and user:
        kwargs["username"] = user
        kwargs["password"] = password or ""

    paths = read_paths(csv_path)
    fs, root = fsspec.core.url_to_fs(url, **kwargs)
    root = root.rstrip("/")

    tty = None
    if not YES and not DRY:
        try:
            tty = open("/dev/tty", "r+")
        except OSError:
            print("error: no terminal to confirm on; use --yes", file=sys.stderr)
            return 2

    deleted = skipped = failed = 0
    ask = not YES
    print(f"{len(paths)} files listed for {url}" + (" (dry run)" if DRY else ""))
    for n, rel in enumerate(paths, 1):
        label = f"[{n}/{len(paths)}] {rel}"
        if unsafe(rel):
            print(f"{label}: refused (absolute or '..' path)")
            failed += 1
            continue
        full = f"{root}/{rel}"
        try:
            if not fs.exists(full):
                print(f"{label}: not found, skipped")
                skipped += 1
                continue
            if fs.isdir(full):
                print(f"{label}: is a directory, refused")
                failed += 1
                continue
        except Exception as exc:
            print(f"{label}: error: {exc}")
            failed += 1
            continue
        if DRY:
            print(f"{label}: would delete")
            continue
        if ask:
            tty.write(f"{label}\n  Delete? [y/N/a/q] ")
            tty.flush()
            ans = tty.readline().strip().lower()
            if ans == "q":
                print("quit")
                break
            if ans == "a":
                ask = False
            elif ans != "y":
                print(f"{label}: skipped")
                skipped += 1
                continue
        try:
            fs.rm_file(full)
            print(f"{label}: deleted")
            deleted += 1
        except Exception as exc:
            print(f"{label}: FAILED: {exc}")
            failed += 1

    print(f"deleted {deleted}, skipped {skipped}, failed {failed}")
    return 1 if failed else 0


sys.exit(main(sys.argv[1], sys.argv[2]))
PY
