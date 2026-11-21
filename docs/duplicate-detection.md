# Design: near-duplicate image detection

Status: proposed. Extends "Identify Duplicates"
(`storage.find_duplicate_photos`, `GET /api/photos/duplicates/csv`).

## Problem

Today a duplicate means an identical `photos.checksum` (SHA-256 of the raw
bytes). That finds byte-for-byte copies and nothing else. The production
library also holds the same picture as:

| Variant                                  | Bytes | Pixels | Caught today |
|------------------------------------------|-------|--------|--------------|
| Exact copy                               | same  | same   | yes          |
| Resize (web/email/export size)           | differ | differ | no           |
| Re-encode (JPEG quality change, re-save) | differ | ~same  | no           |
| Format change (HEIC ↔ JPEG ↔ PNG ↔ WebP) | differ | ~same  | no           |
| Metadata-only change (EXIF stripped)     | differ | same   | no           |

A cryptographic hash cannot help: any change to the bytes changes the whole
digest. We need a fingerprint of what the image *looks like*, compared by
distance rather than equality.

**Out of scope:** crops, mirrors, heavy edits/filters, "same scene, different
shot" (burst frames). These are deliberately not matched; see
"False positives".

**Invariant (CLAUDE.md):** this feature only reads. It reports candidates; it
never deletes or modifies a file on the photos backend, and does not delete DB
rows either. Acting on the report stays a manual step.

## Approach

1. **Fingerprint** each photo with a 64-bit perceptual hash (dHash) of its
   *decoded, EXIF-transposed* pixels. Format and file size drop out because the
   hash is computed from pixels; scale drops out because the image is shrunk to
   a tiny fixed grid first.
2. **Find candidates** by Hamming distance between hashes, using a banded
   index so it does not need N² comparisons.
3. **Verify** each candidate pair with cheap checks on data already in
   Postgres (aspect ratio, a pixel-level comparison of the stored thumbnails).
4. **Classify and report** each surviving pair, picking the best-quality file
   as the "original".

Exact checksum matching stays as tier 0, unchanged and authoritative.

### 1. Fingerprint: `perceptual.py`

New module, depends only on Pillow/numpy (no `storage`/`database` imports, so
`database.py` can call it from the backfill without a circular import).

```
dhash(image) -> int | None
  g = image.convert("L")            # grayscale; alpha flattened onto white first
  g = g.resize((9, 8), LANCZOS)     # 72 pixels
  if stddev(g) < FLAT_STDDEV:       # blank / solid-colour image
      return None
  bit(i) = g[row, col] > g[row, col+1]   # 8 rows x 8 horizontal gradients
  return 64-bit int, stored as signed BIGINT
```

Why dHash: it compares neighbouring brightness, so it is insensitive to
resizing, JPEG/HEIC compression noise, and global brightness/colour-profile
shifts (the usual difference between a HEIC and its JPEG export). It is also
about 10 lines and fast. pHash (DCT) is slightly more tolerant of blur but
costs more; start with dHash and revisit if the eval below says so.

**Input = the stored thumbnail, not the original.** The 320 px thumbnail is
already decoded, EXIF-transposed (`sync._sync_one_file`), and in Postgres.
Hashing from it means:

- no second decode of the original, and no extra backend reads for SMB/S3
  (the expensive part of this library);
- one code path for new files and for backfilling existing rows (just
  `SELECT thumbnail`, hash, `UPDATE`);
- the hash is a pure function of the thumbnail, so it is reproducible.

Cost: two resizes of the same picture reach the thumbnail through different
source resolutions, adding a little noise. That is exactly what the distance
threshold absorbs. Photos with no thumbnail (undecodable) get no hash, same
as a NULL checksum today: never reported as a duplicate of anything.

Flat-image guard: a solid or near-solid image hashes to all zeros/ones and
would "match" every other blank. `FLAT_STDDEV` leaves such rows with
`phash = NULL`.

### 2. Schema

```sql
ALTER TABLE photos ADD COLUMN IF NOT EXISTS phash BIGINT;
```

Added to both `database.init_db()` and `db/create_tables.sql` (per CLAUDE.md,
no migrations framework) and documented in `docs/database-schema.md`.
`METADATA_COLUMNS` is unchanged: the hash is internal and is not returned to
the web UI.

Population:

- **New/changed files:** `sync._sync_one_file` computes it right after the
  thumbnail, inside the existing decode block's results (no extra I/O). When
  `content_changed`, `phash` is cleared with the other derived fields, so a
  stale hash never survives new content.
- **Existing rows:** `database.backfill_data()` hashes rows with
  `thumbnail IS NOT NULL AND phash IS NULL` in batches of
  `BACKFILL_BATCH_SIZE`, committing per batch, on the existing background
  thread. Do not put this in `init_db()` (the crash-loop lesson in CLAUDE.md).
  Rows whose guard returned `None` would be retried on every start; track them
  with a sentinel (e.g. `phash` unset plus a `phash_done BOOLEAN`), or accept
  the cheap re-hash. Prefer the boolean, mirroring `face_detection_done`.

### 3. Candidate search

`find_similar_photos(max_distance)` in `storage.py`:

1. Load `(id, filename, width, height, size_bytes, created_at, checksum, phash)`
   for all rows with `phash IS NOT NULL`. A 1M-photo library is about
   8 MB of hashes; this is a one-shot in-memory job, no thumbnail blobs.
2. **Banding (pigeonhole):** split each 64-bit hash into 8 bands of 8 bits.
   Two hashes within Hamming distance ≤ 7 must agree exactly on at least one
   band. Bucket by `(band_index, band_value)`; only photos sharing a bucket
   are compared in full (`popcount(a ^ b)`, numpy). Cost is roughly linear in
   N for realistic libraries instead of N²/2.
3. Keep pairs with `distance ≤ max_distance`.

Postgres could do this with `bit_count((a # b)::bit(64))`, but that is a
full scan per probe without a custom index; the in-memory approach is simpler
and fast enough. If memory ever matters, the same band scheme maps onto 8
indexed `SMALLINT` columns.

### 4. Verification

Hash distance alone over-matches (see below). Each candidate pair must also
pass:

- **Aspect ratio:** `|w1/h1 - w2/h2| / (w1/h1) ≤ 2%`, using the EXIF-corrected
  `width`/`height` already stored. A resize preserves aspect ratio; a crop
  does not. (Rows with NULL dimensions skip this check.)
- **Pixel agreement:** decode both stored thumbnails, resize both to 32×32
  grayscale, and require normalised cross-correlation ≥ `0.95`
  (`LUMINARY_DUPLICATE_MIN_CORRELATION`). This is far more discriminating than
  64 bits and still needs no backend reads, just two thumbnail blobs per
  surviving candidate pair (only candidates, not the whole library).

### 5. Classification and choosing the original

Each reported pair gets a **match type**, most specific first:

| Match type  | Rule                                                                 |
|-------------|----------------------------------------------------------------------|
| `exact`     | same `checksum`                                                      |
| `reformat`  | different file extension/format, same width × height (±1px)          |
| `resize`    | different width × height                                             |
| `reencode`  | same format and dimensions, different bytes (quality change, EXIF strip) |

Groups: take clusters of mutually-matched photos, and for each cluster choose
the **original** as the best-quality member:

1. most pixels (`width * height`),
2. lossless/original format preferred (PNG/TIFF/HEIC over JPEG over WebP),
3. larger `size_bytes`,
4. earliest `created_at`, then `filename` (the existing deterministic
   tie-break).

This differs on purpose from today's earliest-date rule: for resizes the thing
you'd keep is the biggest file, not the oldest.

**No transitive chaining.** Single-linkage clustering (A~B, B~C ⇒ A, B, C one
group) lets a long drift of slightly different shots collapse into one
cluster. Instead: pick the original, then include a photo only if it matches
*the original* directly. Leftovers are re-clustered among themselves.

### 6. API and UI

- `GET /api/photos/duplicates/csv?mode=exact|similar|all`. The default is `all`;
  `exact` gives today's two-column output.
- CSV columns: `Original Image, Duplicate Image, Match Type, Distance,
  Original Size, Duplicate Size`, where size is `WxH`. The first two columns
  are unchanged, so existing consumers still work; new columns are appended.
- Optional `max_distance` query override for tuning.
- **Decision: stays a synchronous download.** The production library is about
  40k photos: hashes are ~320 KB, candidate search takes seconds, and
  verification (two thumbnail decodes per candidate pair) is the slowest part
  at roughly 10-20s. Make sure nginx's `proxy_read_timeout` is at least 60s.
  The one-time `phash` backfill (~1-2 min) runs on the startup background
  thread. Only if the library grows ~10x or the request starts timing out,
  move it to a job on the shared slot (`jobs.try_claim("duplicates")`,
  cancellable, progress via `GET /api/jobs`) following the
  `geocode.start_refresh` pattern. Don't pre-build that.
- UI: the existing button's caption/help text changes from "byte-identical" to
  "identical and visually matching (resized, re-encoded, other formats)".
  A side-by-side review view is a natural follow-up, not part of this.

### Config

| Variable                              | Default | Meaning                                  |
|---------------------------------------|---------|------------------------------------------|
| `LUMINARY_DUPLICATE_MAX_DISTANCE`     | `6`     | Max Hamming distance (of 64) for a match |
| `LUMINARY_DUPLICATE_MIN_CORRELATION`  | `0.95`  | Thumbnail correlation required to confirm|

Read lazily per call in `config.py`, like the others. Neither number is
verified against this library: treat them as starting points. dHash distance
for the same picture after a resize/re-encode is usually 0-4; unrelated
photos cluster around 32.

## False positives and tuning

- **Burst shots / near-identical frames** can land within a few bits. The
  correlation check and aspect check remove most of them; same `created_at`
  with different `checksum` and *same* dimensions is the signature of a burst
  rather than a resize, so such pairs should be labelled `reencode` only when
  the correlation is very high (≥ 0.99), otherwise dropped.
- **Screenshots and graphics** with large flat areas have low-entropy hashes
  and collide. The flat-image guard handles the extreme; the correlation check
  handles the rest.
- **Threshold choice:** same philosophy as `LUMINARY_FACE_MATCH_THRESHOLD`:
  don't guess a number, measure. The job logs a histogram of pair distances
  at `INFO` (count per distance 0..16) so the real gap between "same picture"
  and "different picture" in this library is visible.

## Testing

- `perceptual.py` unit tests with generated images (as `conftest.write_image`
  does): identical image → distance 0; resized to 50%/25%, JPEG quality 40,
  PNG↔JPEG round trip, all ≤ threshold; a different image ≥ 20; solid colour →
  `None`.
- HEIC↔JPEG via `pillow-heif`'s encoder, if available in the test env.
- `find_similar_photos`: banding returns the same pairs as a brute-force
  all-pairs comparison on a random set (property test; this is the thing most
  likely to be subtly wrong).
- Original selection: a resize pair picks the larger file; a 3-copy cluster
  yields one original and two rows; no transitive chain.
- Router: `mode=exact` output is identical to today's; `mode=all` adds
  similar rows plus the new columns.
- Sync: `phash` populated on insert, cleared and recomputed on content change,
  backfilled for rows that have a thumbnail but no hash.
- Add `make eval-duplicates` (like `eval-search`): given a folder of known
  duplicate/non-duplicate pairs, prints the distance and correlation of each so
  thresholds can be set from real production examples.

## Rollout

1. `perceptual.py`, column, sync population, backfill. Nothing user-visible.
2. `find_similar_photos`, router `mode`, CSV columns, UI text. Review the CSV
   against a real library slice with `make eval-duplicates` and adjust
   thresholds.
3. (Later, optional) background-job form, in-app side-by-side review, and
   tolerance for crops/mirrors via a learned image embedding (e.g. CLIP).
   The existing `photo_embeddings` are text embeddings of descriptions, so
   they can't be reused for this.

## Alternatives considered

- **pHash/wHash:** more blur-tolerant, slower, no clear win for resize/format
  duplicates. Revisit if eval shows misses.
- **Hash from the original file:** a few bits more faithful, but needs a
  second full read of every file over SMB/S3 for little gain.
- **CLIP-style image embeddings + pgvector:** handles crops and edits, but adds
  a heavy model and inference per photo. Overkill for resize/format duplicates;
  it's the upgrade path for "similar", not "same".
- **Compare thumbnails only, no hash:** O(N²) pixel comparisons; the hash is
  what makes candidate search tractable.
