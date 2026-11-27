# Video / moving-image support

Luminary indexes movies that already sit in the photos library, shows them in
the gallery with a poster frame and a video-camera icon, and plays them in the
photo modal. Like everything else, it is **read-only against the photos
backend**: nothing is written, created or deleted there. Posters live in
Postgres (`photos.thumbnail`), exactly like image thumbnails.

## Decisions

1. **Unified table.** Videos are rows in `photos`, so the gallery, dates,
   locations, tags, trips, duplicates and sidebar counts work unchanged. One
   new column, `media_kind`, tells them apart.
2. **No transcoding.** The original is streamed as-is (with HTTP Range
   support) into a native `<video>` element. A browser can only play what it
   can decode: H.264 `.mp4`/`.mov` everywhere, HEVC on Safari and on Chrome/
   Edge machines with hardware decode, VP8/VP9/AV1 `.webm`, H.264 `.mkv` in
   Chrome/Edge. `.avi`, `.wmv`, MPEG-2 and similar don't play; the modal then
   shows the poster, a short explanation and a Download link. No JS player
   widens this (Video.js, Plyr etc. wrap the native element). The player is
   isolated in `VideoPlayer.jsx` so a WASM decoder such as libmedia could be
   slotted in as the fallback later without touching anything else.
3. **Live Photo `.mov` companions are skipped** when a same-stem still
   (`IMG_1234.heic`/`.jpg`/...) sits in the same directory. Set
   `LUMINARY_INDEX_LIVE_PHOTO_VIDEOS=1` to index them anyway.

## Data model

| Column | Meaning |
|---|---|
| `media_kind TEXT NOT NULL DEFAULT 'image'` | `image`, `video`, or `animated` (animated GIF/WebP/APNG: still an `<img>`, but shown with the camera icon) |
| `duration_seconds DOUBLE PRECISION` | videos only; NULL otherwise |

Reused columns: `content_type` (`video/mp4`, `video/quicktime`, ...),
`width`/`height` (display size, rotation applied), `thumbnail` (poster frame,
same 320x320 JPEG), `created_at`/`latitude`/`longitude` (container metadata),
`checksum`.

As with every schema change here there are no migrations: the `ALTER TABLE ...
ADD COLUMN IF NOT EXISTS` is in `database.init_db()`, `db/create_tables.sql` and
`db/update.sql`. The `DEFAULT 'image'` backfills existing rows with no per-row
work (nothing that could outlast the liveness probe). Existing GIF/WebP/PNG
files become `animated` the next time sync re-reads them; a row with no
decode-time classification yet stays `image`.

## Indexing (`sync.py`, `video.py`)

* `sync.VIDEO_EXTENSIONS` = `.mp4 .m4v .mov .webm .mkv .avi`. Discovery
  filters on images + videos. `LUMINARY_INDEX_VIDEOS=0` turns videos off.
* `video.py` wraps the `ffprobe` and `ffmpeg` binaries:
  * `probe(path)` -> duration, display width/height (rotation applied),
    `creation_time`, GPS (ISO 6709 `location` tag, as written by iPhones).
  * `poster(path, duration)` -> one JPEG frame (about 1s in, or 10% of a short
    clip), rotation applied automatically by ffmpeg.
* ffmpeg needs a seekable local file. A local backend path is used directly;
  any other backend (SMB/S3/HTTP) is copied to a temp file (capped by
  `LUMINARY_VIDEO_MAX_PROBE_BYTES`, default 2 GiB; larger files are indexed
  with metadata only) and removed afterwards. Video work runs under its own
  small semaphore (`LUMINARY_VIDEO_CONCURRENCY`, default 2) so a few big
  files can't fill the disk.
* Date order: container `creation_time`, then the file's modification time,
  then NULL (the index time is never used).
* A missing `ffmpeg`/`ffprobe`, an unreadable or corrupt file, or a clip too
  big to probe never fails the sync: the file is indexed with metadata only
  (no poster, width/height or duration), logged at `WARNING`, and retried
  next sync because the thumbnail is missing.
* A video is **not** captioned, face- or pet-scanned: `face_detection_done`
  and `pet_detection_done` are set TRUE at insert, and the face full-refresh
  and pet backfill jobs skip `media_kind = 'video'`. Its description is
  composed by `enrich.py` from the filename and location only (no caption), so
  videos are still found by semantic search.
* The poster feeds the perceptual hash like any thumbnail.

## Serving (`routers/photos.py`, `storage.py`)

* `GET /api/photos/{id}/thumbnail` is unchanged (it returns the poster).
* `GET /api/photos/{id}/original` honours `Range: bytes=a-b` (also `a-` and
  `-n`) with `206`/`Content-Range`/`Accept-Ranges: bytes`, `416` for an
  unsatisfiable range, and plain `200` without the header. Browsers won't seek
  (or, for some MP4s, start) without it. `storage.stream_original(metadata,
  start, end)` seeks with `fs.open(...).seek()`; still read-only, still
  through `_with_reconnect`. Images are unaffected.
* `POST /api/photos/{id}/chat` on a video returns `422` (the vision model is
  not sent video). Library-wide chat search returns videos like any photo.

## Web

* `PhotoCard`: a video-camera icon in the bottom-right corner of every
  `video` and `animated` tile, always visible, drawn in literal colours on a
  dark scrim. Videos also show their duration (`0:42`) beside it. When a tile
  also has a search-score badge, the badge moves up above the icon.
* `PhotoModal`: a `video` photo renders `VideoPlayer` (native `<video
  controls playsInline preload="metadata" poster=...>`) in place of the
  `<img>`; the face overlay, chat boxes and hover-zoom are image-only. On a
  playback error the player shows the poster, "This browser can't play this
  format" and a link to download the original. Arrow-key navigation still
  works; space/arrows are left to the player while it has focus, and playback
  stops on navigate/close.

## Deployment

* `api/Containerfile` installs `ffmpeg` (provides `ffprobe`).
* Local runs need `ffmpeg` on `PATH` (`brew install ffmpeg`); `make test-api`
  video tests are skipped without it.
* Temp files go under `TMPDIR`; the pod's `/data` volume is already writable
  (emptyDir or PVC), and the Helm chart points `TMPDIR` at it.

## Configuration

| Variable | Default | |
|---|---|---|
| `LUMINARY_INDEX_VIDEOS` | `1` | `0` skips videos entirely |
| `LUMINARY_INDEX_LIVE_PHOTO_VIDEOS` | `0` | `1` also indexes `.mov` companions of Live Photos |
| `LUMINARY_VIDEO_MAX_PROBE_BYTES` | `2147483648` | larger files are indexed without poster/duration |
| `LUMINARY_VIDEO_CONCURRENCY` | `2` | simultaneous video probes |

## Not in v1

Transcoding, captioning/faces/pets for video, scrubbing previews, and pairing
a Live Photo's still with its clip.
