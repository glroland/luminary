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

Request flow: `main.py` wires CORS + the `health` and `photos` routers under
`/api`, with lifespan calling `database.init_db()` on startup. `routers/photos.py`
is thin — all actual work (file I/O, thumbnailing, SQLite) lives in
`storage.py`:

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
  `https://` (see README's config table). Originals are read directly from
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

SQLite + on-disk files means the API is single-replica only (see
`api.replicaCount` note in Helm values) — don't add code that assumes
multiple API pods share storage without also changing the storage layer.
The same applies to `sync.py`'s in-memory progress state and thread: it
isn't shared across replicas either.

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
