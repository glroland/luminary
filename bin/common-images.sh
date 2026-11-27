#!/usr/bin/env bash
#
# Find the files that exist in both of two image stores and write them to a CSV.
#
# Usage: bin/common-images.sh <store-a> <store-b> <output.csv> [--no-header]
#   e.g. bin/common-images.sh smb://host/photo /Volumes/backup/photos common.csv
#
# A file is "in both" when a file with the same name (the last path component,
# directory ignored) and the same size in bytes exists in each store. The CSV
# has two columns, "path_a" and "path_b": the file's path (directory + file
# name) relative to each store's root, every value double-quoted, sorted. The
# store path itself is never included. If one name+size occurs several times in
# a store, every combination is listed. --no-header omits the header row.
#
# Read-only: it only lists the stores, never writes to them. Each store may be
# anything fsspec understands (local path, smb://, s3://, https://, ...).
# Credentials for smb:// come from LUMINARY_PHOTOS_USERNAME /
# LUMINARY_PHOTOS_PASSWORD (never put them in the URL) and apply to both
# stores. Uses the project venv's Python (target/venv; run `make install`).
#
# Exit codes: 0 done, 1 failed, 2 bad usage.

set -uo pipefail

HEADER=1
ARGS=()
for a in "$@"; do
    case "$a" in
        --no-header) HEADER=0 ;;
        -h|--help) sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        --*) echo "unknown option: $a" >&2; exit 2 ;;
        *) ARGS+=("$a") ;;
    esac
done
if [ "${#ARGS[@]}" -ne 3 ]; then
    echo "usage: $0 <store-a> <store-b> <output.csv> [--no-header]" >&2
    exit 2
fi
export COMMON_IMAGES_HEADER="$HEADER"

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
from collections import defaultdict

import fsspec

USERNAME_PASSWORD_PROTOCOLS = {"smb"}


def index(url):
    """Return {(file name, size): [relative path, ...]} for one store."""
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
    out = defaultdict(list)
    for path, info in fs.find(root, detail=True).items():
        if info.get("type") == "directory":
            continue
        rel = path[len(prefix):] if path.startswith(prefix) else path.lstrip("/")
        out[(rel.rsplit("/", 1)[-1], info.get("size"))].append(rel)
    return out


def main(url_a, url_b, out_path):
    try:
        a = index(url_a)
    except Exception as exc:
        print(f"error: could not list {url_a}: {exc}", file=sys.stderr)
        return 1
    try:
        b = index(url_b)
    except Exception as exc:
        print(f"error: could not list {url_b}: {exc}", file=sys.stderr)
        return 1

    rows = sorted(
        (pa, pb)
        for key in a.keys() & b.keys()
        for pa in a[key]
        for pb in b[key]
    )

    try:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, quoting=csv.QUOTE_ALL)
            if os.environ.get("COMMON_IMAGES_HEADER") == "1":
                w.writerow(["path_a", "path_b"])
            w.writerows(rows)
    except OSError as exc:
        print(f"error: could not write {out_path}: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {len(rows)} common files to {out_path}")
    return 0


sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
PY
