#!/usr/bin/env bash
#
# Move one image from one repo to another, copy-then-delete.
#
# Usage: bin/move-image.sh <source-repo-url> <target-repo-url> <relative-path>
#   e.g. bin/move-image.sh smb://host/images smb://host/backup vacation/2019/beach.jpg
#
# Transactional in the sense that the source is only deleted after the copy
# has been written AND verified:
#   1. copy source -> "<target>/<path>.part" (a temp name, never the final one)
#   2. verify the temp file's size matches the source's
#   3. rename the temp file into place
#   4. verify the final file exists with the right size
#   5. only then delete the source
# Any failure before step 5 leaves the source untouched and removes the temp
# file. If the target already has the file, nothing is overwritten and the
# source is left alone.
#
# Repos may be anything fsspec understands (local path, smb://, s3://, ...),
# same as LUMINARY_PHOTOS_URI. For smb:// credentials come from
# LUMINARY_PHOTOS_USERNAME / LUMINARY_PHOTOS_PASSWORD (never put them in the
# URL). Uses the project venv's Python (target/venv; run `make install`).
#
# NOTE: this script deletes from the *source* repo. It is a standalone
# operator tool; the Luminary API itself never writes to or deletes from its
# photos backend, and nothing here changes that. Run `POST /api/sync`
# afterwards to reconcile the index.

set -euo pipefail

DEBUG=0
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --debug) DEBUG=1 ;;
        *) ARGS+=("$arg") ;;
    esac
done
if [ "${#ARGS[@]}" -ne 3 ]; then
    echo "usage: $0 <source-repo-url> <target-repo-url> <relative-path> [--debug]" >&2
    exit 2
fi
export MOVE_IMAGE_DEBUG="$DEBUG"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${LUMINARY_PYTHON:-$ROOT/target/venv/bin/python}"

if [ ! -x "$PYTHON" ]; then
    echo "error: $PYTHON not found; run 'make install' first" >&2
    exit 1
fi

exec "$PYTHON" -I - "${ARGS[@]}" <<'PY'
import logging
import os
import sys

import fsspec

CHUNK = 1024 * 1024
USERNAME_PASSWORD_PROTOCOLS = {"smb"}
DEBUG = os.environ.get("MOVE_IMAGE_DEBUG") == "1"


def dbg(msg):
    if DEBUG:
        print(f"[debug] {msg}", flush=True)


def fail(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def open_fs(url):
    kwargs = {}
    protocol = url.split("://", 1)[0] if "://" in url else "file"
    user = os.environ.get("LUMINARY_PHOTOS_USERNAME")
    password = os.environ.get("LUMINARY_PHOTOS_PASSWORD")
    if protocol in USERNAME_PASSWORD_PROTOCOLS and user:
        kwargs["username"] = user
        kwargs["password"] = password or ""
    # never print the password itself
    dbg(f"connecting to {url} (protocol={protocol}, "
        f"auth={'username/password' if kwargs else 'none'})")
    fs, root = fsspec.core.url_to_fs(url, **kwargs)
    dbg(f"  resolved root: {root}")
    return fs, root.rstrip("/")


def main(source_url, target_url, rel):
    rel = rel.strip("/")
    if not rel or any(p in ("", ".", "..") for p in rel.split("/")):
        fail(f"invalid relative path: {rel!r}")

    src_fs, src_root = open_fs(source_url)
    dst_fs, dst_root = open_fs(target_url)
    src = f"{src_root}/{rel}"
    dst = f"{dst_root}/{rel}"
    tmp = f"{dst}.part"

    dbg(f"source path: {src}")
    dbg(f"target path: {dst} (temp: {tmp})")

    dbg("checking source exists and reading its size")
    try:
        src_size = src_fs.info(src)["size"]
    except FileNotFoundError:
        dbg("  result: source not found")
        fail(f"source does not exist: {source_url}/{rel}")
    dbg(f"  result: source exists, {src_size} bytes")

    dbg("checking target does not already exist")
    if dst_fs.exists(dst):
        dbg("  result: target exists")
        fail(f"target already exists, refusing to overwrite: {target_url}/{rel}")
    dbg("  result: target is free")

    # 1. copy to a temp name on the target
    try:
        parent = dst.rsplit("/", 1)[0]
        dbg(f"step 1: creating target directory {parent}")
        dst_fs.makedirs(parent, exist_ok=True)
        dbg(f"step 1: copying {src} -> {tmp}")
        copied = 0
        with src_fs.open(src, "rb") as r, dst_fs.open(tmp, "wb") as w:
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                w.write(chunk)
                copied += len(chunk)
        dbg(f"  result: wrote {copied} bytes to temp file")

        # 2. verify the copy
        tmp_size = dst_fs.info(tmp)["size"]
        dbg(f"step 2: verifying temp file size: {tmp_size} (expected {src_size})")
        if tmp_size != src_size:
            raise IOError(f"size mismatch after copy: {tmp_size} != {src_size}")
        dbg("  result: sizes match")

        # 3. move into place, 4. verify the final file
        dbg(f"step 3: renaming {tmp} -> {dst}")
        dst_fs.mv(tmp, dst)
        final_size = dst_fs.info(dst)["size"]
        dbg(f"step 4: verifying final file size: {final_size} (expected {src_size})")
        if final_size != src_size:
            raise IOError(f"size mismatch after rename: {final_size} != {src_size}")
        dbg("  result: sizes match")
    except Exception as exc:
        dbg(f"  result: FAILED ({type(exc).__name__}: {exc})")
        for leftover in (tmp,):
            try:
                if dst_fs.exists(leftover):
                    dbg(f"cleanup: removing temp file {leftover}")
                    dst_fs.rm(leftover)
                    dbg("  result: removed")
                else:
                    dbg("cleanup: no temp file to remove")
            except Exception as cexc:
                dbg(f"  result: cleanup failed ({cexc})")
        dbg("source was not touched")
        fail(f"copy failed, source left untouched: {exc}")

    # 5. copy is confirmed; now (and only now) delete the original
    dbg(f"step 5: deleting source {src}")
    try:
        src_fs.rm(src)
        dbg("  result: source deleted")
    except Exception as exc:
        dbg(f"  result: FAILED to delete source ({type(exc).__name__}: {exc})")
        fail(
            f"copied OK to {target_url}/{rel} but could not delete the source "
            f"({exc}); file now exists in BOTH repos"
        )

    print(f"moved {source_url}/{rel} -> {target_url}/{rel} ({src_size} bytes)")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        main(*sys.argv[1:4])
    except SystemExit:
        raise
    except BaseException as exc:  # e.g. backend unreachable, bad URL, Ctrl-C
        fail(f"{type(exc).__name__}: {exc}")
    sys.exit(0)
PY
