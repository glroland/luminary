# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Luminary is a simple photo browser: index an existing, externally-managed
photo library, browse it in a gallery, view full-size.

**Hard invariant: the API never writes to, creates in, or deletes from the
configured photos backend (`LUMINARY_PHOTOS_URI`), under any circumstance.**
There is no upload endpoint that writes a file there (`POST /api/photos`
always returns `403`), and `DELETE /api/photos/{id}` only removes the SQLite
row — it never touches the source file. This is intentional and was an
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
  SQLite; originals are read from wherever `LUMINARY_PHOTOS_URI` points (a
  PVC/NFS mount in the cluster, or S3/SMB/HTTP) and never written back to.

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
make helm-lint   # helm lint deploy/helm/luminary
make helm-template
make clean       # rm -rf target/ (+ web/node_modules, web/dist)
```

Single test, api: `target/venv/bin/python -m pytest api/tests/test_photos.py::test_name -v`

Single test, web: `cd web && npx vitest run src/App.test.jsx -t "test name"`

There's no `api` virtualenv activation step baked into other targets —
`run-api` and `test-api` call `target/venv/bin/python`/`uvicorn` directly, so
run commands the same way instead of assuming an activated venv.

All local run/build artifacts — the venv, the local SQLite DB + photo
storage `make run-api` defaults to (`LUMINARY_PHOTOS_URI`/
`LUMINARY_DATABASE_PATH` set to paths under `target/data/`), and the pytest
cache (`cache_dir` in `api/pyproject.toml`) — live under `target/` at the
repo root (gitignored), so `make clean` is just `rm -rf target/`. The one
exception is `web/node_modules`/`web/dist`: left in `web/` rather than moved
under `target/`, since `web/Containerfile`'s container build (`COPY
--from=build /app/dist ...`) expects `dist` there and Node's module
resolution requires `node_modules` to live alongside `package.json` — both
are still covered by `make clean` explicitly, just not by the `target/`
rename.

## API architecture (`api/src/app/`)

Request flow: `main.py` wires CORS + the `health`, `photos`, and `sync`
routers under `/api`, with lifespan calling `database.init_db()` on startup.
`routers/photos.py` is thin — all actual work (file I/O, thumbnailing,
SQLite) lives in `storage.py`/`sync.py`:

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

- `config.py` reads `LUMINARY_PHOTOS_URI`, `LUMINARY_DATABASE_PATH`,
  `LUMINARY_CORS_ORIGINS` from env **lazily per-call** (not cached at import
  time) specifically so tests can override them per-case with
  `monkeypatch.setenv` — see `tests/conftest.py`'s `client` fixture, which
  points both at a fresh `tmp_path` per test.
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
  time and stored as a `BLOB` in SQLite, never written back to the backend —
  this also means thumbnails work on read-only `https://` backends where
  writing a second file wouldn't be possible anyway. The `filename` column
  holds the path *relative to the photos root* exactly as discovered (e.g.
  `vacation/2019/beach.jpg`) — `stream_original` builds the backend path
  directly from it. `created_at` prefers the photo's EXIF
  `DateTimeOriginal`/`DateTimeDigitized`/`DateTime` (falling back to index
  time if absent or unparseable), and `latitude`/`longitude` come from the
  EXIF GPS IFD when present (`_extract_exif`) — both are best-effort and
  null when EXIF is missing. `delete_photo` only ever issues a `DELETE FROM
  photos` — there is deliberately no filesystem call anywhere near it.
  Because the backend may not be a real filesystem, `routers/photos.py`
  serves originals via `StreamingResponse` over `storage.stream_original`
  rather than FastAPI's `FileResponse` (which requires a local path);
  thumbnails are served as a plain `Response` read straight from the
  database. `list_photos`/`get_photo` select an explicit column list
  (`storage.METADATA_COLUMNS`) that excludes the thumbnail blob, so listing
  photos never pulls thumbnail bytes off disk — only `get_thumbnail` does
  that, per photo, on demand. There are no DB migrations (see `database.py`
  below), so an existing local `photos.db` from before a schema change needs
  to be deleted/recreated.
- `database.py` opens a fresh sqlite3 connection per operation (no pooling);
  `init_db()` just creates the `photos` table if missing — there are no
  migrations, so schema changes need a manual `ALTER TABLE` added here.
- `sync.py` is the *only* way photos enter the index — it reconciles SQLite
  against whatever `fs.find()` discovers under the photos root, filtered to
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

SQLite + on-disk files means the API is single-replica only (see
`api.replicaCount` note in Helm values) — don't add code that assumes
multiple API pods share storage without also changing the storage layer.
The same applies to `sync.py`'s in-memory progress state and thread: it
isn't shared across replicas either. Florence-2's loaded model (`chat.py`'s
module-level `_florence_model`/`_florence_processor`, lazily populated and
cached) is also per-pod, in-memory state — same caveat.

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

## Web architecture (`web/src/`)

- `api/client.js` is the only place that talks to the backend — all
  components call through it, never `fetch` directly. `API_BASE` defaults to
  `/api` (same-origin via the nginx proxy in prod, or Vite's dev proxy
  locally) and can be overridden with `VITE_API_BASE_URL`.
- `App.jsx` holds top-level state (photo list, loading/error, sync status) and
  composes `components/{PhotoGrid,PhotoCard,PhotoModal,SyncStatus}.jsx`.
- The header's Refresh control is `SyncStatus.jsx`: it renders a plain button
  when idle, or (while a backend sync is running/just finished) a small
  progress bar polled from `GET /api/sync/status` every second. Clicking it
  calls `POST /api/sync` and sets local state to show the progress UI
  immediately — redundant with the fact that the backend response already
  reports `"running"` synchronously (see `sync.py`'s `try_start()`), but it
  saves one round-trip before the UI updates. On `"completed"` it refetches
  the photo list; either terminal state
  (`"completed"`/`"error"`) is shown briefly then cleared back to the
  button. `App.jsx` also checks sync status once on mount so a page reload
  mid-sync resumes polling instead of losing the progress UI.
- Tests mock the API client module rather than hitting a real server (see
  `App.test.jsx`) to cover loading, empty, populated, error, and sync-progress
  states; the sync-progress test uses fake timers (`vi.useFakeTimers()` +
  `vi.advanceTimersByTimeAsync`) to drive the 1s poll loop deterministically.
- `PhotoModal.jsx` (opened by clicking a photo card) is a two-pane layout
  filling most of the viewport (`.photo-modal`: 94vw × 92vh) — a large
  image pane plus a sidebar composing `PhotoMap.jsx` and `PhotoChat.jsx`.
  Stacks vertically under 700px width.
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
