#!/usr/bin/env bash
#
# Recursively list every file under a photo repo URI and write them to a CSV,
# one row per file.
#
# Usage: bin/list-images.sh <repo-url> <output.csv> [--no-header]
#   e.g. bin/list-images.sh smb://host/photo files.csv
#
# The CSV has a single column, "path": each file's path (directory + file name)
# relative to the repo root, sorted, every value double-quoted. That is the
# same shape bin/move-images.sh reads (column 1 = relative path), so a list
# made here can be edited down and fed straight to it. All files are listed,
# not just images. --no-header omits the header row.
#
# Read-only: it only lists the repo, never writes to it. The repo may be
# anything fsspec understands (local path, smb://, s3://, https://, ...).
# Credentials for smb:// come from LUMINARY_PHOTOS_USERNAME /
# LUMINARY_PHOTOS_PASSWORD (never put them in the URL). Uses the project
# venv's Python (target/venv; run `make install`).
#
# Exit codes: 0 listed, 1 failed, 2 bad usage.

set -uo pipefail

HEADER=1
ARGS=()
for a in "$@"; do
    case "$a" in
        --no-header) HEADER=0 ;;
        -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        --*) echo "unknown option: $a" >&2; exit 2 ;;
        *) ARGS+=("$a") ;;
    esac
done
if [ "${#ARGS[@]}" -ne 2 ]; then
    echo "usage: $0 <repo-url> <output.csv> [--no-header]" >&2
    exit 2
fi
export LIST_IMAGES_HEADER="$HEADER"

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


def main(url, out_path):
    kwargs = {}
    protocol = url.split("://", 1)[0] if "://" in url else "file"
    user = os.environ.get("LUMINARY_PHOTOS_USERNAME")
    password = os.environ.get("LUMINARY_PHOTOS_PASSWORD")
    if protocol in USERNAME_PASSWORD_PROTOCOLS and user:
        kwargs["username"] = user
        kwargs["password"] = password or ""

    try:
        fs, root = fsspec.core.url_to_fs(url, **kwargs)
        root = root.rstrip("/")
        found = fs.find(root)
    except Exception as exc:
        print(f"error: could not list {url}: {exc}", file=sys.stderr)
        return 1

    prefix = root + "/"
    rels = sorted(
        p[len(prefix):] if p.startswith(prefix) else p.lstrip("/")
        for p in found
    )

    try:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, quoting=csv.QUOTE_ALL)
            if os.environ.get("LIST_IMAGES_HEADER") == "1":
                w.writerow(["path"])
            for rel in rels:
                w.writerow([rel])
    except OSError as exc:
        print(f"error: could not write {out_path}: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {len(rels)} files to {out_path}")
    return 0


sys.exit(main(sys.argv[1], sys.argv[2]))
PY
