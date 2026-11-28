#!/usr/bin/env bash
#
# Set the date of one image whose date is wrong.
#
# Usage: bin/set-image-date.sh <file> [new-date] [--yes] [--debug]
#   e.g. bin/set-image-date.sh smb://host/images/vacation/2019/beach.jpg
#        bin/set-image-date.sh smb://host/images/beach.jpg "2019-07-04 14:30:00"
#        bin/set-image-date.sh /Volumes/images/beach.jpg 2019-07-04
#
# <file> is a local path (including an already-mounted share) or anything
# fsspec understands, notably smb://host/share/path/file.jpg, the same as
# LUMINARY_PHOTOS_URI. For smb:// the credentials come from
# LUMINARY_PHOTOS_USERNAME / LUMINARY_PHOTOS_PASSWORD (never put them in the
# URL). If new-date is omitted the current dates are shown and you are asked.
# Accepted: "YYYY-MM-DD", "YYYY-MM-DD HH:MM[:SS]" (also with "T", or ":" in
# the date part, as EXIF writes it). A bare date means 12:00:00.
#
# What it does (lossless -- the pixels are never re-encoded):
#   1. downloads the file to a temp dir
#   2. keeps a copy of the original in ~/.luminary-date-backups/
#   3. rewrites the embedded capture dates with exiftool (-AllDates:
#      DateTimeOriginal, CreateDate, ModifyDate). Luminary reads the EXIF
#      date first, so this is the date it will show.
#   4. after you confirm, uploads it as "<name>.part", verifies the size and
#      renames it over the original (a failed upload never damages the original)
#
# The file's own modification time is NOT changed on smb:// (it can't be set
# through fsspec); it only matters for a photo with no embedded date at all.
# Then run POST /api/sync (the Refresh button): the file's size changes, so
# it is re-read and its date updated.
#
# Requires exiftool (brew install exiftool) and the project venv
# (`make install`).
#
# NOTE: this script WRITES to the photos library. It is a standalone operator
# tool; the Luminary API itself never writes to its photos backend, and
# nothing here changes that.

set -euo pipefail

DEBUG=0
YES=0
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --debug) DEBUG=1 ;;
        --yes) YES=1 ;;
        *) ARGS+=("$arg") ;;
    esac
done
if [ "${#ARGS[@]}" -lt 1 ] || [ "${#ARGS[@]}" -gt 2 ]; then
    echo "usage: $0 <file> [new-date] [--yes] [--debug]" >&2
    exit 2
fi
FILE="${ARGS[0]}"
NEW_DATE="${ARGS[1]:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${LUMINARY_PYTHON:-$ROOT/target/venv/bin/python}"

if [ ! -x "$PYTHON" ]; then
    echo "error: $PYTHON not found; run 'make install' first" >&2
    exit 1
fi
if ! command -v exiftool >/dev/null 2>&1; then
    echo "error: exiftool not found; install it (macOS: brew install exiftool)" >&2
    exit 1
fi

dbg() { [ "$DEBUG" = 1 ] && echo "[debug] $*" >&2 || true; }

# fsspec helper: "get" downloads to a local file, "info" prints the size,
# "put" uploads a local file over the remote one (temp name, verify, rename).
fs() {
    FS_DEBUG="$DEBUG" "$PYTHON" -I - "$@" <<'PY'
import os
import sys

import fsspec

CHUNK = 1024 * 1024
action, url, local = sys.argv[1], sys.argv[2], sys.argv[3]
protocol = url.split("://", 1)[0] if "://" in url else "file"
kwargs = {}
user = os.environ.get("LUMINARY_PHOTOS_USERNAME")
if protocol == "smb" and user:
    kwargs["username"] = user
    kwargs["password"] = os.environ.get("LUMINARY_PHOTOS_PASSWORD", "")
fs, path = fsspec.core.url_to_fs(url, **kwargs)


def fail(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


try:
    if action == "get":
        if not fs.exists(path):
            fail(f"does not exist: {url}")
        with fs.open(path, "rb") as r, open(local, "wb") as w:
            while chunk := r.read(CHUNK):
                w.write(chunk)
    elif action == "put":
        want = os.path.getsize(local)
        tmp = path + ".part"
        try:
            with open(local, "rb") as r, fs.open(tmp, "wb") as w:
                while chunk := r.read(CHUNK):
                    w.write(chunk)
            got = fs.info(tmp)["size"]
            if got != want:
                raise IOError(f"size mismatch after upload: {got} != {want}")
            fs.rm_file(path)  # rm_file, not rm: rm() glob-expands [ ] * ?
            fs.mv(tmp, path)
            final = fs.info(path)["size"]
            if final != want:
                raise IOError(f"size mismatch after rename: {final} != {want}")
        except Exception as exc:
            try:
                if fs.exists(tmp):
                    fs.rm_file(tmp)
            except Exception:
                pass
            fail(f"upload failed ({exc}); if the original is missing, the "
                 f"edited copy is in the backup directory")
    else:
        fail(f"unknown action {action}")
except SystemExit:
    raise
except Exception as exc:
    fail(f"{type(exc).__name__}: {exc}")
PY
}

# Normalise user input to EXIF form "YYYY:MM:DD HH:MM:SS"; empty on failure.
normalise() {
    local s="$1" d t
    s="${s/T/ }"
    d="${s%% *}"
    t="12:00:00"
    [ "$s" != "$d" ] && t="${s#* }"
    d="${d//-/:}"
    case "$t" in ??:??) t="$t:00" ;; esac
    if [[ "$d" =~ ^[0-9]{4}:(0[1-9]|1[0-2]):(0[1-9]|[12][0-9]|3[01])$ ]] \
        && [[ "$t" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$ ]]; then
        echo "$d $t"
    fi
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
NAME="$(basename "$FILE")"
LOCAL="$WORK/$NAME"

echo "Reading $FILE ..."
fs get "$FILE" "$LOCAL"

echo
echo "Current dates:"
exiftool -a -G1 -s -time:all -FileSize "$LOCAL" | grep -v -E "^\[(File|ExifTool)\] *(FileAccess|FileInode|FilePermissions|FileTypeExt|FileName|Directory)" || true
echo

if [ -z "$NEW_DATE" ]; then
    read -r -p "New date (YYYY-MM-DD or YYYY-MM-DD HH:MM[:SS]): " NEW_DATE
fi
EXIF_DATE="$(normalise "$NEW_DATE")"
if [ -z "$EXIF_DATE" ]; then
    echo "error: can't understand date: $NEW_DATE" >&2
    exit 2
fi

# Keep the untouched original before changing anything.
BACKUP_DIR="$HOME/.luminary-date-backups"
mkdir -p "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/$(date +%Y%m%d-%H%M%S)-$NAME"
cp -p "$LOCAL" "$BACKUP"
dbg "backup: $BACKUP"

dbg "exiftool -AllDates=$EXIF_DATE"
exiftool -q -overwrite_original "-AllDates=$EXIF_DATE" "$LOCAL"

echo "After the change:"
exiftool -a -G1 -s -time:all "$LOCAL"
echo

if [ "$YES" != 1 ]; then
    read -r -p "Write this over $FILE? [y/N] " answer
    case "$answer" in
        y|Y|yes|YES) ;;
        *) echo "Cancelled; nothing was changed."; exit 0 ;;
    esac
fi

fs put "$FILE" "$LOCAL"
echo "Done. Date set to $EXIF_DATE. Original saved at $BACKUP"
echo "Run a sync (POST /api/sync / the Refresh button) to update the index."
