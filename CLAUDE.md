# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Luminary is a simple photo management app: upload, browse in a gallery, view
full-size, delete. Two independently deployable components, each its own
container image, deployed together via Helm/ArgoCD on OpenShift:

- **`web/`** — React SPA (Vite). In production, served as a static bundle by
  unprivileged nginx, which also reverse-proxies `/api/*` to the API service
  so the browser only ever talks to one origin.
- **`api/`** — FastAPI service. Photo metadata in SQLite; original images and
  generated thumbnails on disk under a configurable data directory (a PVC in
  the cluster).

The two components only know about each other through the `/api` HTTP
contract — no shared code, no shared types.

## Commands

```bash
make install     # creates api/.venv, installs web/node_modules
make test        # pytest (api) + vitest (web)
make test-api    # api/.venv/bin/python -m pytest api/tests -v
make test-web    # cd web && npm run test
make lint        # eslint on web/src (no Python linter configured)
make run-api     # uvicorn --reload on :8080 (PYTHONPATH=src)
make run-web     # vite dev server on :5173, proxies /api -> :8080
make build       # builds luminary-api and luminary-web container images
make helm-lint   # helm lint deploy/helm/luminary
make helm-template
```

Single test, api: `api/.venv/bin/python -m pytest api/tests/test_photos.py::test_name -v`

Single test, web: `cd web && npx vitest run src/App.test.jsx -t "test name"`

There's no `api` virtualenv activation step baked into other targets —
`run-api` and `test-api` call `api/.venv/bin/python`/`uvicorn` directly, so
run commands the same way instead of assuming an activated venv.

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
- `storage.py` owns the photo lifecycle, and resolves `LUMINARY_PHOTOS_URI`
  through [fsspec](https://filesystem-spec.readthedocs.io/) (`fsspec.core.url_to_fs`)
  rather than touching `pathlib` directly — the URI can be a bare local
  path/NFS mount, `s3://`, `smb://`, or read-only `https://` (see README's
  config table). On upload it writes the original to
  `{root}/originals/{uuid}{ext}`, tries to generate a 320x320 JPEG thumbnail
  at `{root}/thumbnails/{uuid}.jpg` via Pillow (reading/writing through
  `fs.open()`, never assuming a local path), and always records metadata in
  SQLite even if thumbnailing fails (non-image or corrupt upload still gets
  stored — thumbnail is best-effort). Because the backend may not be a real
  filesystem, `routers/photos.py` serves originals/thumbnails via
  `StreamingResponse` over `storage.stream_original`/`stream_thumbnail`
  rather than FastAPI's `FileResponse` (which requires a local path).
- `database.py` opens a fresh sqlite3 connection per operation (no pooling);
  `init_db()` just creates the `photos` table if missing — there are no
  migrations, so schema changes need a manual `ALTER TABLE` added here.
- Photo IDs are UUIDs; original file extension is preserved from the upload
  filename so `find_original_path` has to glob (`{uuid}.*`) rather than know
  the extension up front.

SQLite + on-disk files means the API is single-replica only (see
`api.replicaCount` note in Helm values) — don't add code that assumes
multiple API pods share storage without also changing the storage layer.

## Web architecture (`web/src/`)

- `api/client.js` is the only place that talks to the backend — all
  components call through it, never `fetch` directly. `API_BASE` defaults to
  `/api` (same-origin via the nginx proxy in prod, or Vite's dev proxy
  locally) and can be overridden with `VITE_API_BASE_URL`.
- `App.jsx` holds top-level state (photo list, loading/error) and composes
  `components/{PhotoGrid,PhotoCard,PhotoModal,UploadButton}.jsx`.
- Tests mock the API client module rather than hitting a real server (see
  `App.test.jsx`) to cover loading, empty, populated, and error states.

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
