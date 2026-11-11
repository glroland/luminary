# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Luminary is a simple photo browser: index an existing, externally-managed
photo library, browse it in a gallery, view full-size.

**Hard invariant: the API never writes to, creates in, or deletes from the
configured photos backend (`LUMINARY_PHOTOS_URI`), under any circumstance.**
There is no upload endpoint that writes a file there (`POST /api/photos`
always returns `403`), and `DELETE /api/photos/{id}` only removes the
Postgres row — it never touches the source file. This is intentional and was an
explicit, direct requirement, not an incidental design choice — treat any
change that would add a write/delete/mkdir call against that backend as a
regression to flag, not a reasonable refactor. See storage.py's and sync.py's
module docstrings and README's "Data safety" section. The only way photos
enter the index is `POST /api/sync` scanning what's already there.

Two independently deployable components, each its own container image,
deployed together via Helm/ArgoCD on OpenShift:

- **`web/`** — React SPA (Vite). In production, served as a static bundle by
  unprivileged nginx, which also reverse-proxies `/api/*` to the API service
  so the browser only ever talks to one origin.
- **`api/`** — FastAPI service. Photo metadata + generated thumbnails in
  Postgres (a remote server managed independently of this repo's Helm
  chart/ArgoCD app — see `db/` and the README's "Database" section);
  originals are read from wherever `LUMINARY_PHOTOS_URI` points (a PVC/NFS
  mount in the cluster, or S3/SMB/HTTP) and never written back to.

The two components only know about each other through the `/api` HTTP
contract — no shared code, no shared types.

## Commands

```bash
make install     # creates target/venv, installs web/node_modules
make test        # pytest (api) + vitest (web)
make test-api    # target/venv/bin/python -m pytest api/tests -v
make test-web    # cd web && npm run test
make lint        # eslint on web/src (no Python linter configured)
make run-api     # uvicorn --reload on :8080 (PYTHONPATH=api/src)
make run-web     # vite dev server on :5173, proxies /api -> :8080
make build       # builds luminary-api and luminary-web container images
make db          # provisions the Postgres database + role + tables (db/)
make db-recreate # drops and recreates them
make db-drop     # drops them
make db-update   # applies db/update.sql's schema change in place, then clears it (no drop/rebuild)
make helm-lint   # helm lint deploy/helm/luminary
make helm-template
make clean       # rm -rf target/ (+ web/node_modules, web/dist)
```

Single test, api: `target/venv/bin/python -m pytest api/tests/test_photos.py::test_name -v`

Single test, web: `cd web && npx vitest run src/App.test.jsx -t "test name"`

There's no `api` virtualenv activation step baked into other targets —
`run-api` and `test-api` call `target/venv/bin/python`/`uvicorn` directly, so
run commands the same way instead of assuming an activated venv.

All local run/build artifacts — the venv and the pytest cache (`cache_dir`
in `api/pyproject.toml`) — live under `target/` at the repo root
(gitignored), so `make clean` is just `rm -rf target/`. Photo metadata lives
in a separate, remote Postgres (`LUMINARY_DATABASE_*`, provisioned via
`make db`/`db/` — see the README's "Database" section) rather than under
`target/`. The one exception to the `target/` rule is `web/node_modules`/
`web/dist`: left in `web/` rather than moved under `target/`, since
`web/Containerfile`'s container build (`COPY --from=build /app/dist ...`)
expects `dist` there and Node's module resolution requires `node_modules`
to live alongside `package.json` — both are still covered by `make clean`
explicitly, just not by the `target/` rename.

## API architecture (`api/src/app/`)

Request flow: `main.py` wires CORS + the `health`, `photos`, and `sync`
routers under `/api`, with lifespan calling `database.init_db()` on startup.
`routers/photos.py` is thin — all actual work (file I/O, thumbnailing,
Postgres) lives in `storage.py`/`sync.py`:

- Logging: standard-library `logging`, configured once in
  `logging_config.configure_logging()` (called at the top of `main.py`,
  before the app is constructed) via `logging.basicConfig()`, level from
  `LUMINARY_LOG_LEVEL` (default `INFO`). Every module gets its own
  `logger = logging.getLogger(__name__)`. `main.py` also registers a global
  `@app.exception_handler(Exception)` so an unhandled error anywhere is
  always logged with a traceback (`logger.exception`) before the generic
  500 goes out — don't rely on a route handler's own try/except to be the
  only thing standing between a bug and a silent failure. `config.redact_uri()`
  strips embedded credentials (`smb://user:pass@...`) before any URI derived
  from `LUMINARY_PHOTOS_URI` is logged — never log `get_photos_uri()`'s
  return value directly.

- `config.py` reads `LUMINARY_PHOTOS_URI`, `LUMINARY_DATABASE_HOST`/`_PORT`/
  `_NAME`/`_USER`/`_PASSWORD`, `LUMINARY_CORS_ORIGINS` from env **lazily
  per-call** (not cached at import time) specifically so tests can override
  them per-case with `monkeypatch.setenv` — see `tests/conftest.py`'s
  `client` fixture, which points the photos URI at a fresh `tmp_path` per
  test. The database connection itself isn't overridden per test —
  `make test-api` points `LUMINARY_DATABASE_NAME` at the dedicated
  `luminary-unittest` database (separate from `luminary-dev`, which
  `make run-api` uses, and from prod's own `luminary` — see the Makefile
  and README's "Database" section) and the fixture truncates `photos`/
  `faces` at the start of each test for isolation instead (there's no
  per-test throwaway file the way `tmp_path` gives SQLite).
- `storage.py` is read-only against the photos backend by construction: grep
  it (or the whole `api/` tree) for `"wb"`/`fs.rm`/`fs.makedirs`/`.write(` if
  you're ever unsure — there should be nothing. It resolves
  `LUMINARY_PHOTOS_URI` through [fsspec](https://filesystem-spec.readthedocs.io/)
  (`fsspec.core.url_to_fs`) rather than touching `pathlib` directly — the URI
  can be a bare local path/NFS mount, `s3://`, `smb://`, or read-only
  `https://` (see README's config table). Auth credentials
  (`LUMINARY_PHOTOS_USERNAME`/`_PASSWORD`) are deliberately kept separate
  from the URI and passed to fsspec as kwargs (`url_to_fs(uri, username=...,
  password=...)`) rather than embedded as `smb://user:pass@host/...` — never
  reintroduce the embedded form, it'd put secrets in something that gets
  logged (even redacted, that's a weaker guarantee than never having them
  there) and defaulted in the Makefile/Helm values. They're only forwarded
  for protocols in `_USERNAME_PASSWORD_PROTOCOLS` (currently just `smb`,
  since that's the fsspec backend whose constructor actually takes
  `username`/`password` kwargs) — S3 needs nothing from us (boto3's
  standard credential chain handles `AWS_ACCESS_KEY_ID`/env/IAM role on its
  own); don't add a protocol here without checking its fsspec filesystem's
  actual constructor kwargs first. Originals are read directly from
  wherever they already live under the photos root — there's no app-managed
  subdirectory (an earlier version wrote uploads under `{root}/originals/`;
  that convention is gone along with the upload path it existed for).
  Thumbnails (320x320 JPEG, best-effort via Pillow) are generated at index
  time and stored as a `BYTEA` in Postgres, never written back to the
  backend — this also means thumbnails work on read-only `https://` backends where
  writing a second file wouldn't be possible anyway. The `filename` column
  holds the path *relative to the photos root* exactly as discovered (e.g.
  `vacation/2019/beach.jpg`) — `stream_original` builds the backend path
  directly from it. `created_at` prefers the photo's EXIF
  `DateTimeOriginal`/`DateTimeDigitized`/`DateTime` (falling back to index
  time if absent or unparseable), and `latitude`/`longitude` come from the
  EXIF GPS IFD when present (`_extract_exif`) — both are best-effort and
  null when EXIF is missing. `location_name` — a short, human-readable
  reverse-geocoded place description derived from that same latitude/
  longitude — is a separate module, `geocode.py` (see its own entry
  below), called from `sync.py`, not `storage.py`/`_extract_exif` itself.
  `delete_photo` only ever issues a `DELETE FROM
  photos` — there is deliberately no filesystem call anywhere near it.
  Because the backend may not be a real filesystem, `routers/photos.py`
  serves originals via `StreamingResponse` over `storage.stream_original`
  rather than FastAPI's `FileResponse` (which requires a local path);
  thumbnails are served as a plain `Response` read straight from the
  database. `list_photos`/`get_photo` select an explicit column list
  (`storage.METADATA_COLUMNS`) that excludes the thumbnail blob, so listing
  photos never pulls thumbnail bytes off disk — only `get_thumbnail` does
  that, per photo, on demand. There are no DB migrations (see `database.py`
  below) — `init_db()`'s `CREATE TABLE IF NOT EXISTS` only covers a
  brand-new database; a schema change needs a manual `ALTER TABLE` added to
  both `init_db()` and `db/create_tables.sql` together.
- `database.py` opens a fresh `psycopg` connection per operation (no
  pooling), row factory set to `psycopg.rows.dict_row` so query results
  come back as plain dicts (matching `storage.py`/`sync.py`'s `dict(row)`
  calls, carried over from the pre-Postgres `sqlite3.Row` behavior);
  `init_db()` creates the `photos`/`faces` tables if missing. Connection
  parameters come from `config.get_database_host/_port/_name/_user/
  _password()` — a remote server provisioned via `db/`'s SQL (see the
  README's "Database" section and `make db`/`make db-recreate`), not
  something this app stands up itself.
- `sync.py` is the *only* way photos enter the index — it reconciles
  Postgres against whatever `fs.find()` discovers under the photos root, filtered to
  a fixed set of image extensions (`sync.IMAGE_EXTENSIONS`) so non-photo
  files in a real, messy library (sidecar files, `.DS_Store`, etc.) don't
  get indexed as broken photos. Read-only like `storage.py` — see
  `routers/sync.py`'s `POST /api/sync` / `GET /api/sync/status` and the
  README's "Data safety" section. `filename` (the path relative to the
  photos root) is the correlation key between a discovered file and its DB
  row; a file is "unchanged" if its size matches the DB row, "updated"
  (re-extracts width/height/EXIF/thumbnail, same `id`) if the size differs,
  "inserted" (new `id`) if there's no row for that path yet, and any DB row
  whose path wasn't seen in the scan gets its *row* deleted (never the
  file — if it's still there, the next sync re-inserts it).

  `_sync_one_file` runs `ImageOps.exif_transpose()` on the opened image
  *before* reading `.size` or building the thumbnail — camera sensors
  commonly store pixels in landscape with an EXIF `Orientation` tag saying
  how to rotate for display (e.g. a phone held sideways for a portrait
  shot), and `_extract_exif` is called on the pre-transpose image since
  `exif_transpose` doesn't guarantee the other EXIF tags (DateTime/GPS)
  survive on the returned copy. Skipping the transpose was a real bug: both
  the indexed `width`/`height` and the generated thumbnail came out
  landscape for portrait photos, since the raw sensor pixel grid is
  landscape regardless of the orientation tag. Don't read `image.size` or
  thumbnail straight off the `Image.open()` result again without
  transposing first.

  Whenever GPS coordinates are (re)extracted (`content_changed`, same
  condition as `latitude`/`longitude` themselves), `_sync_one_file` also
  reverse-geocodes them into `location_name` via `_describe_location` —
  but only *after* the `with fs.open(...)` block has closed, deliberately:
  that's a network call to a third-party service (`geocode.py`), not file
  I/O, and shouldn't hold a backend file handle/connection (e.g. an SMB
  session) open while it runs.

  The reconciliation runs in its **own thread with its own `asyncio.run()`
  event loop** (`sync.start_sync()`), not as a bare `asyncio.create_task()`
  on the API's request-handling loop — the latter only makes progress while
  that loop happens to be ticking, which isn't guaranteed in general (e.g.
  it stalls indefinitely under FastAPI's `TestClient` between requests,
  which is how this was caught). Within that thread, files are fanned out
  across a `ThreadPoolExecutor` via `asyncio.gather`/`run_in_executor` so
  slow, network-bound backends (S3/SMB/NFS) aren't processed one file at a
  time.

  Progress (`processed`/`total`/`inserted`/`updated`/`deleted`/`status`) is
  a thread-safe module-level singleton (`sync.progress`, lock-protected).
  **`start_sync()` must flip status to `"running"` synchronously, inside
  `progress.try_start()`, before returning** — this isn't just tidiness: an
  earlier version only set `"running"` from inside the background thread,
  so a caller (test or real frontend) that called `POST /api/sync` and
  immediately polled `GET /api/sync/status` could read the *previous* run's
  stale terminal status, believe the new sync was already done, and move on
  while the real one kept running unobserved — corrupting whatever state it
  touched next (a later test's "idle" assertion, or a second sync call
  treating an existing photo as new again). `try_start()`'s single locked
  check-and-transition fixes both that race and a second one it closes for
  free: two near-simultaneous `start_sync()` calls could otherwise both see
  "not running" and both spawn a thread, double-processing the same files.
  Don't reintroduce a window between "decide to start" and "mark as
  running". Tests must call `sync.progress.reset()` between cases (see
  `conftest.py`'s `client` fixture) since it isn't reset per-request; shared
  test helpers (`write_image`, `sync_and_wait`) also live in `conftest.py`
  and are imported with `from conftest import ...` in test files.

  A per-file failure (corrupt/unreadable image) is caught in
  `_sync_one_file` and logged at `WARNING` with the offending relative path
  and a traceback (`exc_info=True`) — the file still gets indexed with
  metadata only (no width/height/thumbnail), it just doesn't fail the whole
  sync. A failure in `_run_sync` itself (backend unreachable, etc.) is
  caught at the top level, logged at `ERROR` via `logger.exception` (full
  traceback), and surfaces as `status: "error"` with the message in
  `GET /api/sync/status`'s `error` field. Both of these were previously
  silent (bare `except: pass` / `progress.fail()` with no log call) —
  don't reintroduce a silent except around backend I/O in this module.

Moving metadata to Postgres means multiple API replicas *could* share that
part of the storage layer, but on-disk originals/thumbnails were never the
only obstacle: `sync.py`'s in-memory progress state and background thread
are per-process and aren't shared across replicas either, so
`api.replicaCount > 1` still isn't supported (see Helm values' note) —
don't add code that assumes otherwise without also addressing that.
Florence-2's loaded model (`chat.py`'s
module-level `_florence_model`/`_florence_processor`, lazily populated and
cached) is also per-pod, in-memory state — same caveat.

- `geocode.py` reverse-geocodes a photo's EXIF GPS coordinates into
  `photos.location_name`, a short human-readable place description (e.g.
  "Boone, North Carolina", "Grandfather Mountain State Park, Blowing
  Rock, North Carolina", or "Target, 123 North Point Pkwy, Alpharetta,
  Georgia" — see `_describe`'s own docstring for the street slot's
  placement) stored alongside the raw `latitude`/`longitude` —
  called from `sync.py`'s `_describe_location`, only when those
  coordinates are themselves (re)extracted (i.e. `content_changed`, same
  as `latitude`/`longitude` — no *automatic* backfill for this field
  during a sync, deliberately, same reasoning those two already have; see
  `start_refresh()` below for the manually-triggered one). Uses OpenStreetMap's
  Nominatim reverse-geocoding API — free, no API key, the same OSM service
  `PhotoMap.jsx` already depends on for map tiles — rather than a
  commercial geocoder requiring an account/billing.
  `LUMINARY_GEOCODE_BASE_URL` can point this at a self-hosted Nominatim
  instance instead (`config.get_geocode_base_url`); the public instance's
  usage policy caps requests at **one per second**, enforced globally
  (not per-thread) by `geocode._rate_limit()` — since `sync.py` fans files
  out across `MAX_CONCURRENCY` worker threads, this rate limit, not the
  network call itself, becomes the long pole of a sync for a GPS-heavy
  library against the public instance. Nominatim's policy also requires a
  descriptive `User-Agent` (`LUMINARY_GEOCODE_USER_AGENT`,
  `config.get_geocode_user_agent`) identifying the calling application.
  Deliberately uses only `urllib` (stdlib) rather than adding a new HTTP
  client dependency for what's a small, optional, best-effort feature —
  `geocode.reverse_geocode` raises on any failure (network, timeout,
  malformed JSON, Nominatim's own `{"error": ...}` "nothing here"
  response), and it's `sync._describe_location`'s job to catch that and
  log it, never `sync`'s own failure. `LUMINARY_GEOCODE_ZOOM`
  (`config.get_geocode_zoom`, default `18`, the maximum) picks which OSM
  feature layer the lookup resolves to (low = broad administrative area,
  high = up to building/named-business-level) — like
  `LUMINARY_FACE_MATCH_THRESHOLD` elsewhere, this has no single
  verified-correct value across very different photo subjects; treat it
  as a starting point to tune against your own library's real photos
  (lower it if a mostly-outdoor/park library ends up with overly narrow
  results). `geocode._PLACE_KEYS` is the ordered set of Nominatim address
  keys checked for a "notable place" name to lead with — it includes
  `shop`/`mall`/`office` (alongside `national_park`/`tourism`/`amenity`/
  etc.) specifically so a named business mapped as its own point/building
  in OSM (e.g. a Target) can surface as the location name instead of just
  its containing street/neighborhood; whether that happens for a given
  photo depends on GPS precision plus OSM's crowd-sourced coverage at
  that spot, not just this zoom/key config.

  `geocode.start_refresh()` (`routers/locations.py`'s "Refresh Locations"
  button, `POST /api/locations/refresh`) is the bulk, manually-triggered
  counterpart to the per-photo call above: it re-geocodes every photo
  that already has `latitude`/`longitude`, not just ones just
  (re)extracted during a sync — the only way an already-indexed photo
  picks up a `LUMINARY_GEOCODE_ZOOM`/`_PLACE_KEYS` change (e.g. the
  shop/street-address additions above) without its underlying file
  changing. Same background-job shape as `sync.py`'s/`recognition.py`'s/
  `enrich.py`'s own `start_refresh()` (own thread + event loop, atomic
  `try_start()`, work fanned out across a thread pool) — copy that
  pattern, don't reinvent it. `_refresh_one` writes back whatever
  `reverse_geocode` returns unconditionally, *including* `None` on a
  resultless-but-successful lookup — matching `sync.py`'s own
  `_describe_location` exactly, so a refresh reproduces what a fresh sync
  of the same coordinates would have written; only an exception (network/
  timeout/malformed response) leaves `location_name` untouched. Still
  subject to the same one-request/second cap against the public Nominatim
  instance as a sync is, since both go through the same `_rate_limit()`.

- `enrich.py` composes `photos.description` — the description actually
  shown in the UI — by having a general-purpose LLM (any
  OpenAI-*compatible* chat endpoint, the same `LUMINARY_OPENAI_*` config
  `chat.py`'s `"openai"` backend already uses) rewrite whichever facts are
  already on hand for a photo into one short, natural-language
  description: its Florence-2 caption (`vlm_description` — see below, this
  is the column that used to be called `description` and held that raw
  caption directly), the names of any individuals currently tagged in the
  photo, `location_name`, and the filename/path. `enrich_description`
  never looks at the image again — only at text already in the database —
  which is what makes it cheap enough to call synchronously, inline, from
  several different request/job code paths (same reasoning
  `POST /api/photos/{id}/chat` already makes its own openai call
  synchronously as part of handling a request).

  `enrich_description(photo_id)` is called everywhere the facts it
  composes from can change for a photo: `sync.py`'s
  `_run_florence_pipeline` right after a fresh `vlm_description`/face set
  is written (even before anyone is tagged — `individuals` is just empty
  at that point); `people.py`'s `tag_face`/`untag_face`, since a face's
  identity changed; `recognition.py`'s auto-tagging, once per *photo* (not
  once per face matched — `enrich_description` already reads every tagged
  individual for a photo in one query) after a "Refresh Faces" run
  auto-matches at least one of a photo's faces; and `people.py`'s
  `delete_individual`, for every photo that had a face tagged to the
  individual being deleted (photo ids collected *before* the bulk
  `UPDATE ... SET person_id = NULL` that would otherwise make them
  unfindable). Best-effort throughout, same shape as `geocode.py`/
  `chat.py`'s own derived fields: a failure (missing config, network
  error, malformed response) is logged and leaves `description` as
  whatever it already was — not cleared to `NULL` — never raised to any
  of those callers. None of them treat this as something whose failure
  should fail the tag/untag/sync/refresh it's attached to.

  The split into `vlm_description` (Florence-2's raw caption, not shown in
  the UI) and `description` (this module's rewrite, what's actually
  displayed) is itself a schema change on an existing column — see
  `database.py`'s `init_db()` and `db/create_tables.sql`'s `DO $$ ... $$`
  block, which renames a pre-existing `description` column to
  `vlm_description` (preserving its data) before re-adding a fresh
  `description`, guarded so it only fires once per database (a database
  already past this point, or created fresh after it, is untouched).

- `chat.py` backs `POST /api/photos/{photo_id}/chat`
  (`routers/photos.py:chat_about_photo`), which reads the full original via
  `storage.read_original_bytes` (unlike `stream_original`, chat needs the
  whole image in memory, not a chunked HTTP response) and calls
  `chat.ask(backend, photo_bytes, content_type, message, history)`.
  `backend` is `"openai"` or `"florence"`, picked per-request by the
  frontend's checkbox — there's no server-side default/fallback between
  them. Validated as a `Literal["openai", "florence"]` on `ChatRequest`
  (`models.py`), not by `chat.ask()`'s own `ValueError` check — an
  unrecognized value is rejected as a `422` by FastAPI's request parsing
  before the handler body even runs. That's deliberate, not incidental: an
  earlier version let `chat.ask()`'s `ValueError` (meant only for "unknown
  backend") be caught by a bare `except ValueError` in the router and
  mapped to `400`, which also silently caught `pydantic.ValidationError`
  bubbling up from inside the `openai` SDK (e.g. parsing a non-conforming
  response from a self-hosted OpenAI-compatible server — it **is** a
  `ValueError` subclass) and misreported a real backend failure as a
  malformed request. `chat_about_photo` must not have an `except
  ValueError` branch — let anything chat.ask() raises other than
  `ChatNotConfigured` fall through to the generic `except Exception` → `502`
  (full traceback goes to the log either way).

  Both backends' real dependencies (`openai`; `torch`/`transformers`/
  `timm`/`einops`) are imported **lazily inside the functions that use
  them**, not at module top — `chat.py` itself, and the rest of the app,
  must keep importing cleanly whether or not either is installed. Don't
  move those imports to module level. Missing config or missing deps both
  surface as `chat.ChatNotConfigured`, mapped to a `503` with a message
  naming what's missing — never let either reach the client as a bare
  `ImportError`/`500`.

  **Streaming** (`LUMINARY_OPENAI_STREAMING`, off by default,
  `config.get_openai_streaming()`): only the `openai` backend supports it —
  `chat.should_stream(backend)` is `False` for `"florence"` regardless of
  the flag, since Florence-2's API has no token-by-token output to stream.
  When `should_stream()` is true, `chat_about_photo` calls
  `chat.ask_stream(...)` instead of `chat.ask(...)` and wraps it in a
  `StreamingResponse(..., media_type="text/plain")` instead of returning
  `ChatResponse` JSON — the frontend tells which one it got from the
  response's own `content-type` header, not from a client-side copy of the
  setting. `ask_stream()` → `_ask_openai_stream()` is written so
  `_prepare_openai_call()` (config validation + building the `openai`
  client/messages) runs **eagerly, before the generator is returned** —
  not deferred into the generator function itself — specifically so
  `ChatNotConfigured` (and anything else from that setup step) still raises
  synchronously when the router calls `chat.ask_stream(...)`, while the
  `StreamingResponse` hasn't been constructed yet and the router can still
  turn it into a normal `503`/`502` `HTTPException`. Don't collapse
  `_ask_openai_stream` back into a single generator function — once the
  HTTP response has actually started streaming, the status code can no
  longer change, so a failure at that point (rare — a genuine network
  failure mid-generation, not a config problem) is the one case handled by
  yielding an inline `"\n[chat backend failed]"` marker into the stream
  instead, logged server-side either way.

  **OpenAI-compatible path** (`_ask_openai`): `LUMINARY_OPENAI_BASE_URL` has
  no default of our own choosing — leaving it unset falls through to the
  `openai` SDK's own default (`api.openai.com`), which is only correct if
  that's genuinely the target; don't special-case or assume it elsewhere.
  `LUMINARY_OPENAI_MODEL` likewise has no default — a self-hosted server's
  model name is arbitrary, there's nothing sensible to guess. The image is
  base64 data-URL-encoded and attached to the *current* message on every
  call (not just the first turn), so it works the same regardless of
  whether a given OpenAI-compatible server retains image content referenced
  from earlier turns in the conversation. Before encoding,
  `_downscale_for_chat` caps the image's longest side at
  `_MAX_CHAT_IMAGE_DIMENSION` (2048px) — originals indexed from a modern
  phone are commonly 10+ megapixels, and sending one at full resolution to
  a self-hosted multimodal gateway (vLLM and similar) is a real way to get
  an opaque `500` back, since many such servers have a much smaller max
  input resolution than that. Best-effort: if the bytes don't decode as an
  image for any reason, it falls back to the original bytes unchanged
  rather than failing the chat request over this. `_ask_openai`/the
  streaming `generate()` also wrap their `client.chat.completions.create`
  call to log the raw upstream response body
  (`_log_upstream_error_body`) on failure — the `openai` SDK's own
  exception message is sometimes just a bare status code with no detail
  when a self-hosted gateway's error body doesn't match the shape it
  expects, and that body is usually where the actual diagnostic is.

  **Florence-2 path** (`_ask_florence` / `_load_florence`): loads
  `LUMINARY_FLORENCE_MODEL` (default `microsoft/Florence-2-base`) via
  `transformers.AutoModelForCausalLM`/`AutoProcessor` with
  `trust_remote_code=True`, per the
  [model card](https://huggingface.co/microsoft/Florence-2-base)'s sample
  code — lazily, on first use, under a lock, then cached in the module
  globals for the life of the process. **This is not a conversational VQA
  model**: its public API is a fixed set of task-prompt tokens (captioning,
  OCR, object detection, phrase grounding) with no token for open-ended
  question answering. Exactly one task takes free text alongside the task
  token — `<CAPTION_TO_PHRASE_GROUNDING>` (`chat._FLORENCE_GROUNDING_TASK`)
  — and it means "locate this phrase in the image", returning bounding
  box(es), not an answer. `_ask_florence` uses that task with the user's
  message as the phrase whenever the message is non-empty (formatted into
  readable text by `_format_grounding_result`); an empty message instead
  runs `<MORE_DETAILED_CAPTION>` (`chat._FLORENCE_CAPTION_TASK`) for a plain
  description. Don't route the message into any *other* task token without
  re-reading the model card first — none of the rest take a text input, and
  the model wasn't trained to combine them with trailing free text the way
  `run_example()` does for grounding specifically (task token and text
  concatenated with no separator — match that exactly, it's part of the
  trained format, not just a readability choice). `_ask_florence` must not
  `import torch` at its own top level before calling `_load_florence()` —
  that import has to happen after `_load_florence()` has already succeeded
  (confirming torch is actually importable), or a missing-dependency
  environment raises a raw `ImportError` instead of the intended
  `ChatNotConfigured`.

  **Pin `transformers==4.49.0` exactly** — don't bump it without testing
  Florence-2 loads afterward. Its model repo ships its own modeling/config
  code via `trust_remote_code=True`, written against an older transformers
  API; newer releases (confirmed as of `5.21.0`) stopped unconditionally
  setting generation defaults like `forced_bos_token_id` on
  `PretrainedConfig`, which that custom code reads directly and crashes on
  with `AttributeError: 'Florence2LanguageConfig' object has no attribute
  'forced_bos_token_id'` — a load-time failure, not a chat-time one, so it
  doesn't surface as `ChatNotConfigured`. `4.49.0` is confirmed working
  (loaded and generated successfully end-to-end against real
  `microsoft/Florence-2-base` weights on CPU) — see the
  [Florence-2-large-ft discussion board](https://huggingface.co/microsoft/Florence-2-large-ft/discussions/40)
  for multiple independent reports of the same fix.

- `people.py` (`routers/individuals.py`, `routers/faces.py`) is manual face
  tagging — a catalog of named individuals (`individuals` table) and a
  `person_id` column on `faces`, both distinct from `sync.py`'s Florence-2
  face *detection*: this module never detects anything, it only records a
  user's manual assignment of an already-detected face to a person. See
  `docs/database-schema.md`'s `individuals` section for the full schema.
  `people.tag_face` reads face+photo metadata in one connection, reads the
  original off the photos backend with *no* connection held (mirrors
  `storage.py`'s per-operation-connection pattern, just split around
  potentially-slow backend I/O rather than holding a connection through
  it), then writes the update in a second connection. Tagging also crops
  the face rectangle out of the original (same EXIF-transposed pixel space
  `sync.py`'s detection used) and stores it as that individual's
  `representative_thumbnail` — the most recently tagged face wins, there's
  no gallery of every face ever tagged to a person. Re-tagging a face to a
  different person just overwrites `person_id` and the representative
  crop; there's no untag endpoint yet. Note `storage.replace_faces` (full
  rewrite on re-sync, new `id`s every time) means a photo whose content
  changes loses its face tags along with its stale boxes — tagging doesn't
  try to survive that.

  (As of `recognition.py` below, `people.tag_face` also always sets
  `faces.tag_source = 'manual'`, and the old "there's no untag endpoint"
  note above is stale — see `DELETE /api/faces/{id}/tag`/`untag_face` and
  `DELETE /api/individuals/{id}`/`delete_individual`, both of which clear
  `person_id`/`thumbnail`/`tag_source`/`match_confidence` together.)

  (As of `enrich.py` above, `tag_face`/`untag_face`/`delete_individual` all
  also call `enrich.enrich_description` for every photo they affect —
  "who's in this photo" changing is one of the facts `photos.description`
  is composed from.)

- `recognition.py` (`routers/faces.py`'s `POST /api/faces/refresh`,
  `GET /api/faces/refresh/status`) is *automatic* face tagging — "Refresh
  Faces" matches every still-untagged detected face against individuals a
  user has already manually tagged at least one face for, auto-assigning
  `faces.person_id` wherever the match is confident enough. It never
  detects faces itself (that's still `sync.py`/Florence-2); it only decides
  *whose* face an already-detected box is. See `docs/database-schema.md`'s
  "Automatic face recognition" section for the full schema/design writeup
  (the `faces.tag_source`/`match_confidence` columns, the match threshold,
  etc.) — the two invariants worth repeating here since they're easy to
  accidentally break when touching this code: auto-tagging only ever
  writes to faces where `person_id IS NULL` (never overwrites an existing
  tag, manual or auto), and reference embeddings
  (`_reference_embeddings()`) are built *only* from `tag_source = 'manual'`
  faces, never from previously auto-matched ones — an incorrect auto-match
  must never become a future reference that reinforces itself. Whenever
  `_process_photo_faces` auto-tags at least one face in a photo, it also
  calls `enrich.enrich_description(photo_id)` once for that photo (not
  once per face) — same reasoning as `people.py`'s manual tag/untag, see
  `enrich.py`'s entry above.

  `start_refresh(full: bool = False)` has two callers with two very
  differently-scoped behaviors, not one. `sync.py`'s post-sync trigger
  passes the default, `full=False` — incremental, candidates are only
  still-untagged faces, existing faces/tags left completely alone (cheap/
  idempotent after the first pass, run after every sync with no extra
  cost for a library that hasn't changed).

  `routers/faces.py`'s standalone "Refresh Faces" **button** passes
  `full=True`, a deliberately destructive operation: the premise (per
  whoever clicks it) is that `LUMINARY_FACE_DETECTION_THRESHOLD`/
  `LUMINARY_FACE_MIN_SIZE` (`face_detection.py`, above) just changed, so
  every existing face *box* — not just its tag — was detected under
  settings that no longer apply. Before matching starts,
  `_redetect_photo_faces` re-runs `face_detection.detect_faces` for every
  already-indexed photo against the *current* settings and replaces that
  photo's faces (`storage.replace_faces`, deletes-then-reinserts with
  fresh ids — the same "re-detection also wipes tags" behavior `sync.py`'s
  own content-change path already has, just triggered by a settings
  change instead of a file change). `detect_faces` returns, and
  `replace_faces` stores, each box's own `detection_confidence` (MTCNN's
  per-candidate probability, the same score `LUMINARY_FACE_DETECTION_
  THRESHOLD` is compared against — a new `faces` column, `NULL` for any
  row detected before it existed, same as the other `ALTER TABLE ADD
  COLUMN`-never-backfills gaps elsewhere in this file) — unrelated to
  `match_confidence` below, which is a different, later score for a
  different question (*whose* face, not *whether* it's a face).
  `PhotoModal.jsx`'s "f" overlay (below) shows this per box regardless of
  tag state. That means a full refresh discards
  *every* tag in the library, manual or auto, along with the stale boxes
  — a box detected under the old sensitivity may not be the same face (or
  exist at all) under the new one, so a tag pointing at it is exactly as
  stale. `face_detection.ensure_configured()` is checked once, up front,
  *before* any photo is touched — a missing torch/facenet-pytorch
  dependency must fail the whole run before anything is wiped, not get
  discovered photo-by-photo after some faces are already gone with
  nothing to replace them. This is a confirmed, deliberate trade-off
  (confirmed with the person who requested it, given how destructive it
  is): after a full refresh, every individual needs at least one face
  manually re-tagged again before `_reference_embeddings` has anything to
  match future candidates against — `total`/`matched` will legitimately
  show candidates-but-zero-matches until that happens, not a bug.
  `_redetect_photo_faces` only skips `storage.replace_faces` entirely
  (leaving a photo's existing faces completely alone) when its original
  can't be read/decoded this time — "detection didn't run" must not look
  like "detection ran and found zero faces". Every photo that had *any*
  tagged face before the redetect (`_photos_with_tagged_faces`, read
  before any replace_faces call) gets `enrich.enrich_description` called
  afterward, same reasoning as `people.delete_individual`'s own bulk-clear/
  enrich pattern — "who's in this photo" just changed there too,
  independent of whatever the subsequent re-match does or doesn't find.

  Uses facenet-pytorch's `InceptionResnetV1(pretrained="vggface2")` for
  embeddings, deliberately not DeepFace (which would need TensorFlow, a
  *second* multi-GB ML framework, alongside the torch/transformers stack
  Florence-2 above already requires) — see `requirements.txt`'s comment.
  Matching itself only ever uses that embedding half against boxes that
  already exist, from `face_detection.py`'s MTCNN (see that module's own
  entry above for why detection moved off Florence-2's `<OD>` task) —
  this module doesn't call `detect_faces` itself for the *matching* step.
  The one exception is the `full=True` redetect step above, which does
  call `face_detection.detect_faces` directly, through the same MTCNN
  `face_detection.py` already loads for `sync.py`'s own indexing.
  `_embed()`
  calls `_load_embedder()` first (raises `FaceRecognitionNotConfigured`,
  mirroring `chat.ChatNotConfigured`, if torch/facenet-pytorch aren't
  installed) before its own bare `import torch`/`torchvision`/
  `facenet_pytorch` imports — same ordering as `chat.py`'s
  `_run_florence_task` relative to `_load_florence()`. The background job
  itself (`_RefreshProgress`/`progress`/`start_refresh()`) mirrors
  `sync.py`'s `_SyncProgress`/`progress`/`start_sync()` almost verbatim
  (same atomic `try_start()` locking, same "runs in its own thread with its
  own event loop" reasoning, same "errors surface via polling status, the
  POST itself never fails synchronously" shape) — don't reinvent that
  pattern if you add a third background job, copy this one (or sync.py's).
  One deliberate difference: `_RefreshProgress.record(count)` takes a
  count, not always 1 — a whole photo's candidate faces are processed (and
  its original read) together as one executor unit, not one face at a
  time, so progress advances in per-photo batches.

  `LUMINARY_FACE_MATCH_THRESHOLD` (`config.get_face_match_threshold()`,
  default `0.4`) has no verified-correct value — confirmed empirically
  that `InceptionResnetV1`'s output is L2-normalized (`.norm()` ≈ 1.0, so
  cosine similarity is at least a valid metric for it), but there's no
  published "same person" cosine cutoff for this exact model/weights the
  way there is an L2-distance threshold for the original FaceNet paper's
  own embeddings. An earlier default of `0.6` was confirmed too strict in
  practice — real same-person matches scored below it and were silently
  skipped, so a whole run completed having matched nothing. Don't just
  pick another number if this comes up again: `_run_refresh` logs the
  single closest unmatched candidate's actual score at `INFO` whenever a
  run matches 0 faces (`LUMINARY_LOG_LEVEL=DEBUG` logs every candidate's
  score, inside `_process_photo_faces`) specifically so the real number
  from the user's own library drives the threshold, not another guess.
  Both log lines resolve `_individual_names()` and the photo's `filename`
  rather than logging bare `face_id`/`person_id` UUIDs -- there has to be
  something an operator can actually go look at to answer "is this really
  the same person" (the real original ask that exposed the `tag_source`/
  `thumbnail` backfill bugs above in the first place).

  If real same-person pairs keep scoring low even after raising/lowering
  the threshold, suspect the crop before the number: candidates are
  Florence-2's generic object-detection box, with no face-*alignment* step
  (no eye/landmark-based rotation/scaling) before embedding --
  deliberately not using facenet-pytorch's own `MTCNN`, which normally
  provides that alignment as part of detecting+cropping (see this
  module's own docstring on why we skip it). A loose/off-center box can
  suppress genuine similarity scores across the board, not just for one
  photo.

  `_backfill_missing_reference_thumbnails()` (called at the top of
  `_run_refresh`, before `_reference_embeddings()`) exists because of a
  second instance of the exact "`ALTER TABLE ADD COLUMN` never backfills
  existing rows" bug `database.init_db()`'s `tag_source` backfill fixes —
  but one column earlier (`faces.thumbnail`) and one migration earlier, so
  a face manually tagged before *that* column existed has `person_id` set
  but `thumbnail` still `NULL`. This one can't be a bare SQL `UPDATE` like
  the `tag_source` fix (there's nothing to copy it from — it has to be
  re-cropped from the photo's original bytes, real I/O), so it can't live
  in `init_db()` without that module importing `storage`/`people` and
  creating a circular import; it runs lazily instead, once per refresh,
  right before the thumbnails it backfills get read. Without it, such a
  face is invisible to `_reference_embeddings()`'s `thumbnail IS NOT NULL`
  filter, so that individual has *no* reference embedding and can never
  be auto-matched against regardless of the threshold — if a "Refresh
  Faces" run reports "no individuals have a manually tagged face yet"
  despite faces clearly being tagged, check for *this* gap (missing
  thumbnails) before suspecting `tag_source` again.

## Web architecture (`web/src/`)

- `api/client.js` is the only place that talks to the backend — all
  components call through it, never `fetch` directly. `API_BASE` defaults to
  `/api` (same-origin via the nginx proxy in prod, or Vite's dev proxy
  locally) and can be overridden with `VITE_API_BASE_URL`.
- **Theming**: every colour in `styles.css` is a CSS custom property
  defined in the two token blocks at the top of the file (`:root` = dark,
  the default; `:root[data-theme="light"]` = light) — don't hardcode a hex
  value in a rule, add or reuse a token. The handful of literal colours
  left are deliberate: things drawn *on a photo* (bounding boxes, the tile
  caption gradient, the modal's nav buttons) that must look the same in
  either theme. `theme.js` reads/writes the choice (`localStorage`,
  `data-theme` on `<html>`); `main.jsx` applies it before first render and
  the header's toggle button in `App.jsx` flips it. The logo is an inline
  SVG component (`Logo.jsx`), not `<img src="assets/logo.svg">`, so its
  colours can follow the theme too.
- **Gallery** (`PhotoGrid.jsx`/`PhotoCard.jsx`): `App.jsx` hands
  `PhotoGrid` the *whole* filtered+sorted list (`visiblePhotos`, memoised)
  — there is no pagination. `PhotoGrid` renders the first `BATCH_SIZE` and
  reveals another batch whenever an `IntersectionObserver` sentinel at the
  bottom comes into view; that window is local state, so `App.jsx` bumps
  `gridGeneration` (PhotoGrid's `key`, via `resetGrid()`) on every
  filter/sort/search change to restart it from the top — but deliberately
  not when the list is merely reloaded after a sync. `groupBy` (chosen by
  `App.jsx` from the sort order: `"month"` for date sorts, `"folder"` for
  folder sorts, `null` for filename sorts and search results) splits the
  grid into sections with sticky headers; sections are runs of
  *consecutive* photos, so it's only valid when the list is sorted by the
  same thing. A tile has no filename caption (the filename is only the
  thumbnail's alt text, which is also how tests find a tile); everything
  shown on hover — frame, zoom, date/location strip, and the description
  tooltip after a 1s rest — is plain CSS on `.photo-card:hover`, with no
  hover state or timers in React. While the list is first loading,
  `App.jsx` renders shimmering `.photo-skeleton` tiles in its place.
- `App.jsx` holds top-level state: the photo list, loading/error, sync
  status, which top-level `view` is showing (`"gallery"` | `"manage"`),
  whether the sidebar is open, and the currently-selected directory filter
  (`null` = no filter). Composes `Sidebar.jsx`, `PhotoGrid.jsx`,
  `ManagementPage.jsx` (only one of the latter two renders at a time,
  based on `view`), and `PhotoModal.jsx`. `visiblePhotos` filters `photos`
  by `selectedDirectory` (a photo's directory is everything in its
  `filename` before the last `/`; the filter matches that directory *and*
  its subdirectories via a `startsWith` check) before handing them to
  `PhotoGrid.jsx` — the unfiltered `photos` list is still what's passed to
  `Sidebar.jsx` to build its directory tree from, so the tree itself never
  shrinks when filtered.
  `handleNavigateToPhoto(photoId)` (passed down to `ManagementPage.jsx`)
  looks the photo up in the already-loaded `photos` list, switches `view`
  back to `"gallery"`, and opens it in `PhotoModal.jsx` — it does nothing
  if the photo isn't found (e.g. deleted since the list was loaded) rather
  than fetching it separately.
- `Sidebar.jsx` is the collapsible left sidebar: a hamburger toggle button
  (always rendered, `aria-expanded`) plus a `position: fixed` panel+
  backdrop that only exist when open (`sidebar-open` class slides it in via
  `transform`; clicking the backdrop closes it). Contains, top to bottom:
  the Refresh control (`SyncStatus.jsx` — moved here from the header, see
  below), a directory tree built from `photos` by `buildTree()` (a nested
  `{children: {name: node}}` structure derived purely from each photo's
  `filename` — there's no dedicated directory concept in the API) rendered
  via `DirectoryTree.jsx`, and a "Manage" link at the bottom that calls
  `onManage` (→ `App.jsx` sets `view = "manage"`). `DirectoryTree.jsx` is
  recursive and self-contained: each node tracks its own expand/collapsed
  state locally (`useState`, uncontrolled) — only the *selected* directory
  (which filters the grid) is state lifted up to `App.jsx`. Clicking a
  directory's label selects it; clicking its separate chevron toggle
  expands/collapses without selecting, so browsing the tree and filtering
  the grid are independent actions.
- Sidebar presentation (`Sidebar.jsx`/`DirectoryTree.jsx`/`DateTree.jsx`):
  section headers, "All Images" and "Manage" carry a small inline SVG icon
  (`Sidebar.jsx`'s `Icon`, always `aria-hidden`); expand toggles are a
  single "›" glyph that `styles.css` rotates off the button's own
  `aria-expanded` rather than swapping +/− text. Per-row photo counts
  (`buildTree`/`buildDateTree` accumulate `count` on every node, subfolders
  included, matching what selecting that row filters to) are a `data-count`
  attribute rendered by a CSS `::after`, deliberately *not* text in the
  DOM: a row's accessible name stays just its label, and a count like "4"
  can't collide with a day-of-month label of "4". The sidebar's contents
  keep a fixed 280px width while the `<aside>` animates open/closed — keep
  that in step with `.sidebar-open`'s width. The "Search" section is
  rendered in `.sidebar-footer`, pinned above "Manage", not in the
  scrolling `<nav>` with the other sections. The site icon is
  `web/public/favicon.svg` — the logo's circle mark, a third copy of that
  artwork alongside `Logo.jsx` and `assets/logo.svg`.
- `SyncStatus.jsx` itself is unchanged by the above move: it renders a
  plain button when idle, or (while a backend sync is running/just
  finished) a small progress bar polled from `GET /api/sync/status` every
  second. Clicking it calls `POST /api/sync` and sets local state to show
  the progress UI immediately — redundant with the fact that the backend
  response already reports `"running"` synchronously (see `sync.py`'s
  `try_start()`), but it saves one round-trip before the UI updates. On
  `"completed"` it refetches the photo list; either terminal state
  (`"completed"`/`"error"`) is shown briefly then cleared back to the
  button. `App.jsx` also checks sync status once on mount so a page reload
  mid-sync resumes polling instead of losing the progress UI.
- `ManagementPage.jsx` (reached via the sidebar's "Manage" link,
  `view === "manage"` in `App.jsx`) is the individuals catalog's full
  CRUD surface, plus "Refresh Faces" (`recognition.py`'s backend — see its
  CLAUDE.md entry above), "Refresh Descriptions" (`enrich.py`'s backend),
  and "Refresh Locations" (`geocode.py`'s backend, `POST
  /api/locations/refresh` — re-geocodes every photo that already has GPS
  coordinates, since there's no automatic backfill for `location_name`
  the way there isn't one for `latitude`/`longitude`). All three buttons
  live together in the "Refresh metadata" section at the top of the page
  and share the same status-polling/progress-bar shape (own
  `*RefreshStatus` state + poll-interval ref per job, reusing
  `SyncStatus.jsx`'s `.sync-status`/`.sync-progress-track`/
  `.sync-progress-fill` CSS classes rather than the component itself,
  since each needs its own button text) -- copy one of the three if a
  fourth such button is ever added, don't reinvent the polling loop. An
  add-person form, then one `<li>` per
  individual: a chevron toggles its accordion open (lazily fetching
  `GET /api/individuals/{id}/faces` the first time, cached afterward in
  `facesByIndividual` keyed by id — a key's *absence* means "not fetched
  yet", distinct from an empty array), Rename swaps the name for an inline
  input (Save/Cancel), Delete removes the individual entirely. Each
  accordion lists every face tagged to that person — manual or auto,
  `TaggedFace.tag_source` drives a small "auto" badge — as a clickable
  thumbnail (`onNavigateToPhoto(face.photo_id)`, bubbling up to `App.jsx`)
  plus a small "×" remove button that untags (`DELETE /api/faces/{id}/tag`)
  without deleting the underlying detection. "Refresh Faces" polls
  `GET /api/faces/refresh/status` every second exactly like `SyncStatus.jsx`
  polls sync status (same shape, reusing its `.sync-status`/
  `.sync-progress-track`/`.sync-progress-fill` CSS classes rather than the
  component itself, since the button needs different text); on
  `"completed"` it calls `reloadAfterRefresh()`, which re-fetches the
  individuals list and every accordion that's *ever* been expanded this
  page visit (not just currently-expanded ones — simpler than tracking
  that precisely, and collapsed-and-never-opened accordions fetch fresh on
  next expand regardless).
- Tests mock the API client module rather than hitting a real server (see
  `App.test.jsx`) to cover loading, empty, populated, error, and sync-progress
  states; the sync-progress test uses fake timers (`vi.useFakeTimers()` +
  `vi.advanceTimersByTimeAsync`) to drive the 1s poll loop deterministically.
- `PhotoModal.jsx` (opened by clicking a photo card) is a two-pane layout
  filling most of the viewport (`.photo-modal`: 94vw × 92vh) — a large
  image pane plus a sidebar composing `PhotoMap.jsx` and `PhotoChat.jsx`.
  Stacks vertically under 700px width. It opens by scaling up out of the
  clicked tile (`origin` prop → `--modal-origin-x/y`, see the comment on
  `.photo-modal` in `styles.css`, whose 3vw/4vh offsets are tied to the
  modal's size). The optional `photos`/`onNavigate` props (`App.jsx`
  passes `visiblePhotos`) enable stepping between photos: left/right arrow
  keys, edge buttons, and a filmstrip of neighbours under the image.
  Escape closes it. All of the modal's key handling ("f" included) lives
  in one `keydown` listener that ignores keys typed into an input/
  textarea/select — add new shortcuts there, behind that same guard.

  Pressing "f" also drives manual face tagging (`people.py`'s backend --
  see its CLAUDE.md entry above): once face boxes are shown, right-mouse-
  down (`event.button === 2`) on one opens a small viewport-fixed menu
  (`.face-tag-menu`) listing catalogued individuals, anchored at the
  mousedown position via `tagMenu` state; `.photo-modal-bbox-face` has
  `pointer-events: auto` (overriding the `none` the read-only chat/stored
  overlays use) specifically so this mousedown can land on it. Right-click
  rather than middle-click: a Mac trackpad's two-finger click is a
  secondary click at the OS/DOM level (button 2, firing a "contextmenu"
  event), never a true middle click (button 1) — there's no trackpad
  gesture that produces that at all. `handleFaceContextMenu` suppresses
  the browser's native context menu, but only on the face box element
  itself (via `onContextMenu`), so right-click still works normally
  everywhere else in the modal (e.g. to save the photo). While the
  button stays down, each menu item's plain `onMouseEnter`/`onMouseLeave`
  (hover events fire independent of button state) sets `tagMenu.hoveredId`
  to highlight it. Releasing the button is handled by a `window` "mouseup"
  listener, not an element handler — the cursor is typically over a menu
  item, not the original face box, by the time it's released. That
  listener is attached/detached only when the menu opens/closes (a
  `menuOpen` boolean dependency), not on every hover change, to avoid
  re-registering it on every mousemove; it reads the *current* hoveredId
  via `tagMenuRef` (a ref mirrored alongside the `tagMenu` state through a
  `setTagMenu` wrapper) rather than a stale closure over the state at
  attach time. Releasing over a highlighted name calls `client.js`'s
  `tagFace(faceId, personId)`; releasing over nothing (or outside any
  item) just closes the menu with no API call. A tagged face gets a
  distinct border color (`.photo-modal-bbox-face-tagged`) and the
  individual's name as a label. Every face box, tagged or not, also shows
  `faces.detection_confidence` (see `recognition.py`'s entry above) as a
  rounded percentage via a second, bottom-anchored label
  (`.photo-modal-bbox-score`, distinct from `.photo-modal-bbox-label`'s
  `top: -1.5rem` positioning so the two never overlap on the same box) —
  omitted entirely when `null` (a face detected before this column
  existed). The modal's own "People" section (gated
  on `showFaces`, same as the box overlay) only supports adding a new
  individual (a plain name input/button calling `client.js`'s
  `createIndividual`) — renaming/deleting an individual, and browsing/
  untagging every face tagged to them, is `ManagementPage.jsx`'s job (see
  its CLAUDE.md entry above), not this modal's.

  Separately, a "People in this photo" panel (always visible once a photo
  is open — *not* gated on `showFaces`, unlike the catalog-wide "People"
  section above and the box overlay itself) lists every distinct
  individual present in the open photo, manual or auto-tagged alike
  (dedup'd from `faceBoxes` by `person_id`), with a small "auto-detected"
  badge when `tag_source === "auto"`. Because this panel needs
  `faceBoxes`/`individuals` regardless of whether the user ever presses
  "f", those two fetch effects trigger as soon as `photo?.id` is set, not
  only when `showFaces` becomes true (an earlier version gated them both
  behind `showFaces`) — `showFaces` now only controls whether the
  bounding-box overlay (and the catalog-wide "People" section above)
  render, not whether the underlying data is fetched.
- `PhotoMap.jsx` renders an interactive map (Leaflet + OpenStreetMap tiles,
  no API key) centered on `photo.latitude`/`photo.longitude`, or a "No
  location data" placeholder when either is null. Leaflet's default marker
  icon path doesn't resolve through Vite's bundler by default — the PNG
  assets are imported explicitly and wired via `L.Icon.Default.mergeOptions`
  at module load; don't drop that if touching this file.
- `PhotoChat.jsx` talks to `POST /api/photos/{id}/chat` via
  `client.js`'s `chatAboutPhoto(photoId, backend, message, history, onChunk)`.
  A checkbox picks `backend` ("florence" when checked, "openai" otherwise,
  per the two chat paths in `chat.py`) — checking it also shows a static
  hint that Florence-2 always returns a description rather than answering
  the specific question (see `chat.py`'s module docstring for why). Message
  history sent on each call is built from the component's own local
  `messages` state (`{role, text}` per turn, oldest first) — there's no
  server-side conversation state, each request carries the full relevant
  history itself. Resets (`messages`/`draft`/`error`) when `photoId`
  changes. A failed request (e.g. the `503` "not configured" case) surfaces
  `err.message` in a `role="alert"` element rather than silently dropping
  the user's message.

  `chatAboutPhoto` itself (in `client.js`) decides streaming vs. JSON purely
  from the *response's* `content-type` — never a client-side copy of
  `LUMINARY_OPENAI_STREAMING` — so the frontend doesn't need its own
  knowledge of that server setting at all. For a streamed reply it reads
  `response.body` incrementally via `getReader()`/`TextDecoder`, invoking
  `onChunk` per piece and still resolving to the full concatenated text at
  the end, so callers that don't care about incremental display can ignore
  `onChunk` entirely. In `PhotoChat.jsx`, `handleSubmit`'s `onChunk`
  callback grows a single assistant message in place (tracked by index via
  `streamingIndexRef`, a ref rather than state since it must be readable/
  settable synchronously from inside the chunk callback without waiting on
  a render) — if no chunk ever arrives (a plain JSON reply), the full reply
  is appended once at the end instead, so both response shapes render
  through the same message list with no special-casing in the JSX.

## Container builds and proxying

- `web/Containerfile` is a multi-stage build: Vite build stage, then an
  unprivileged nginx image serves the static bundle. `nginx.conf.template` is
  `envsubst`'d at container start using `API_PROXY_TARGET`, so the same image
  can point at any API service URL without rebuilding — don't hardcode the
  API address into the built JS; `VITE_API_BASE_URL` is a build-time value,
  `API_PROXY_TARGET` is the runtime nginx target.
- `api/Containerfile` is `python:3.12-slim`-based.

## Deployment

`deploy/helm/luminary` is the single chart deploying both components
(Deployment+Service+Route per component, plus a PVC for API photo storage).
`deploy/argo-app.yaml` is an ArgoCD `Application` tracking this repo's chart
path with automated sync. `deploy/Jenkinsfile` builds both Containerfiles,
archives images as build artifacts, then pushes to
`registry.home.glroland.com/luminary/`. When changing image build steps or
tags, keep the Jenkinsfile, Helm `values.yaml` defaults, and README's example
`helm upgrade` flags consistent with each other.
