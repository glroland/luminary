# Luminary

A simple photo browser: index an existing photo library, browse it in a
gallery, view full-size. Built as two independently deployable components —
a React single-page app and a Python API — each shipped as its own container
image and deployed together via Helm/ArgoCD on OpenShift.

**The API never writes to, modifies, or deletes anything on the configured
photos backend, under any circumstance.** It only reads from it (to list,
thumbnail, and serve originals) and to index what it finds into its own
local SQLite database. There is no upload endpoint that writes a file there,
and deleting a photo through the API only removes it from the local index —
it never touches the source file. See "Data safety" below.

## Architecture

```
                     ┌─────────────────────────┐
  Browser  ───────▶  │  web (React SPA, nginx) │
                     │   - serves static build │
                     │   - proxies /api/* ───┐ │
                     └────────────────────────┼─┘
                                               ▼
                                 ┌──────────────────────────┐
                                 │  api (FastAPI, Python)    │
                                 │   - photo metadata (SQLite)│
                                 │   - original + thumbnail  │
                                 │     files on disk (PVC)   │
                                 └──────────────────────────┘
```

- **web** — a React single-page app (built with Vite). In production it is
  served as a static bundle by an unprivileged nginx container, which also
  reverse-proxies `/api/*` to the API service so the browser only ever talks
  to one origin.
- **api** — a FastAPI service exposing a small REST API for photo metadata
  and files. It indexes an existing, externally-managed photo library (read
  only — see "Data safety") into SQLite, which also holds generated
  thumbnails; the library itself can be a local path, an NFS mount (backed
  by a PersistentVolumeClaim in the cluster), S3, SMB, or read-only HTTP(S).
- The two components know nothing about each other beyond the `/api` HTTP
  contract, and ship as separate container images so they can be built,
  versioned, and scaled independently.

## Repository layout

```
web/                  React single-page application
  src/                application source
  Containerfile       multi-stage build: Vite build -> nginx-unprivileged
  nginx.conf.template  reverse proxy config (envsubst'd at container start)

api/                  Python API
  src/app/            application source (FastAPI)
  tests/              pytest test suite
  Containerfile       python:3.12-slim runtime image

deploy/
  helm/luminary/      Helm chart deploying both components to OpenShift
  argo-app.yaml       ArgoCD Application pointing at the Helm chart
  Jenkinsfile         Jenkins pipeline: build, package, and push both images

Makefile             install / test / build / deploy helpers
target/               (generated, gitignored) local venv, SQLite DB, photo
                      storage, pytest cache -- see "Local development"
```

## API

| Method | Path                       | Description                        |
|--------|----------------------------|-------------------------------------|
| GET    | `/api/health`              | Liveness/readiness check            |
| GET    | `/api/photos`               | List indexed photo metadata         |
| POST   | `/api/photos`               | Always `403` — uploads are disabled; see "Data safety" |
| GET    | `/api/photos/{id}`          | Get metadata for one photo          |
| GET    | `/api/photos/{id}/thumbnail`| Thumbnail JPEG (served from SQLite) |
| GET    | `/api/photos/{id}/original` | Original file, streamed from the photos backend |
| DELETE | `/api/photos/{id}`          | Remove a photo from the index only — does not touch the source file |
| POST   | `/api/sync`                 | Start a background reconciliation of the SQLite index against the configured photos backend (insert rows for new files, update rows for changed files, delete rows for files no longer present) |
| GET    | `/api/sync/status`          | Progress of the current/last sync: `{status, processed, total, inserted, updated, deleted, error}` |
| POST   | `/api/photos/{id}/chat`     | Ask about a photo — `{backend: "openai"\|"florence", message, history}` → `{reply}`; see "Photo chat" below |

`POST /api/sync` is the only way photos enter the index — point
`LUMINARY_PHOTOS_URI` at a directory (recursively scanned for files with a
common image extension: jpg/jpeg/png/gif/webp/bmp/tif/tiff/heic/heif) and
call this to index what's there. It returns immediately; the reconciliation
runs in a background thread (concurrently across files, since backends like
S3/SMB/NFS are network-bound) and `GET /api/sync/status` is polled for
progress. Only one sync runs at a time — calling `POST /api/sync` while one
is already in flight just returns its current status rather than starting
another.

### Data safety

**The API has no code path that writes to, creates in, or deletes from the
configured photos backend — this holds regardless of what
`LUMINARY_PHOTOS_URI` points at**, including a real, irreplaceable photo
library on NFS/SMB/S3:

- `POST /api/photos` (upload) always returns `403` without touching the
  backend. There is no server-side code that opens a file there for
  writing.
- `DELETE /api/photos/{id}` only deletes the SQLite row for that photo. If
  the source file is still there, the next sync re-indexes it.
- `POST /api/sync` only reads from the backend (`fs.find`/`fs.info`/
  `fs.open(..., "rb")`) to build the index; it never calls a write, delete,
  or directory-creation method on it.

For defense in depth beyond the application layer, also consider mounting
or connecting to the share with read-only credentials at the infrastructure
level (e.g. a read-only SMB account, a read-only S3 IAM policy, or an
NFS export with the `ro` option).

Configuration (environment variables):

| Variable                  | Default            | Purpose                              |
|----------------------------|---------------------|---------------------------------------|
| `LUMINARY_PHOTOS_URI`      | `/data/photos`      | The photo library to index (read-only) |
| `LUMINARY_PHOTOS_USERNAME` | *(none)*            | Backend auth — see below               |
| `LUMINARY_PHOTOS_PASSWORD` | *(none)*            | Backend auth — see below               |
| `LUMINARY_DATABASE_PATH`   | `/data/photos.db`   | SQLite database file                  |
| `LUMINARY_CORS_ORIGINS`    | `*`                 | Comma-separated list of allowed origins |
| `LUMINARY_LOG_LEVEL`       | `INFO`              | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `PORT`                     | `8080`              | Port uvicorn listens on                |

### Authenticating to the photos backend

Credentials are kept **out of `LUMINARY_PHOTOS_URI`** — don't write
`smb://user:pass@host/...` — so the URI itself stays safe to log, default
in a Makefile, or commit. Instead:

- **SMB/CIFS**: set `LUMINARY_PHOTOS_USERNAME` / `LUMINARY_PHOTOS_PASSWORD`;
  the API passes them to `smbprotocol` directly. If your share needs a
  specific domain, qualify the username (e.g. `DOMAIN\alice`).
- **S3**: nothing to configure here — `s3fs`/`boto3` already pick up the
  standard `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`
  env vars or an IAM role automatically.
- **Local path / NFS**: no credentials — access is controlled by the mount
  itself (see "Data safety" above for the `ro` export recommendation).

In Kubernetes, source `LUMINARY_PHOTOS_USERNAME`/`_PASSWORD` from a `Secret`
via `valueFrom.secretKeyRef` rather than a plain value in `values.yaml` —
the Helm chart's `api.env.photosCredentialsSecret` (see
`deploy/helm/luminary/values.yaml`) does this.

The API logs to stdout via Python's standard `logging` module: sync
start/completion (with counts), per-file failures during sync (a file that
can't be read/thumbnailed is logged and skipped rather than failing the
whole sync), rejected upload attempts, database errors, and any unhandled
request error (via a global exception handler, so nothing fails silently
with just a bare 500). `LUMINARY_PHOTOS_URI` is logged once at startup with
any embedded credentials redacted.

`LUMINARY_PHOTOS_URI` is resolved with [fsspec](https://filesystem-spec.readthedocs.io/),
so it accepts any of the following:

| Backend          | Example                                 | Notes                                   |
|-------------------|------------------------------------------|-------------------------------------------|
| Local path / NFS  | `/data/photos`                          | NFS works by mounting the share at the OS level (e.g. the PVC below) and pointing here — there's no separate NFS protocol handler |
| Explicit local    | `file:///data/photos`                   | Same as above, spelled out               |
| Amazon S3         | `s3://my-bucket/photos`                 | Credentials via standard AWS env vars/IAM role |
| SMB/CIFS          | `smb://user:pass@server/share/photos`   | Pure-Python client, no OS-level mount needed |
| HTTP(S)           | `https://host/photos`                   | Read-only, and `POST /api/sync` needs directory-listing support, which plain HTTP(S) servers generally don't have |

The database always stays on local disk regardless of `LUMINARY_PHOTOS_URI`
(SQLite needs real filesystem locking).

### Photo chat

`POST /api/photos/{id}/chat` answers questions about a photo via one of two
backends, picked per-request with the `backend` field (the web UI exposes
this as a checkbox):

- **`"openai"`** — any OpenAI-*compatible* multimodal chat endpoint, via the
  `openai` Python SDK. `LUMINARY_OPENAI_BASE_URL` is never assumed to be
  `api.openai.com` — point it at a self-hosted server (vLLM, Ollama, LM
  Studio, etc.) if that's what you're running. The image is base64-encoded
  and attached to the current message on every call.
- **`"florence"`** — a local [Florence-2](https://huggingface.co/microsoft/Florence-2-base)
  vision-language model, run in-process via `torch`/`transformers`
  (`trust_remote_code=True`, per the model card). The model loads lazily on
  first use and is cached in memory afterward. **Florence-2 is not a
  conversational VQA model** — its public API is a fixed set of task tokens
  (captioning, OCR, object detection, phrase grounding), with no token for
  "answer this arbitrary question". This backend always runs
  `<MORE_DETAILED_CAPTION>` and returns that, regardless of the message text.

| Variable                   | Default                     | Purpose                              |
|-----------------------------|------------------------------|---------------------------------------|
| `LUMINARY_OPENAI_API_KEY`  | *(none)*                    | Required for the `openai` backend     |
| `LUMINARY_OPENAI_BASE_URL` | *(none — SDK default)*      | OpenAI-compatible endpoint URL        |
| `LUMINARY_OPENAI_MODEL`    | *(none)*                    | Required — the model name as your server calls it |
| `LUMINARY_FLORENCE_MODEL`  | `microsoft/Florence-2-base` | Any Florence-2 checkpoint             |
| `LUMINARY_FLORENCE_DEVICE` | `cpu`                       | `cpu` or `cuda`                       |

A backend that's missing required config returns `503` with a message
naming the missing variable, rather than a generic failure.

In Kubernetes, `LUMINARY_OPENAI_API_KEY` is sourced from a `Secret` via the
Helm chart's `api.env.openaiApiKeySecret` (key `apiKey`), same pattern as
the photos-backend credentials above — never put it directly in
`values.yaml`.

Enabling the Florence-2 backend substantially increases the `api` pod's
memory footprint and image size (`torch`/`transformers`/`timm`/`einops`,
plus the downloaded model weights) — bump `api.resources` accordingly if you
turn it on. `api/requirements.txt` has a note on using the CPU-only `torch`
wheel index to avoid pulling in CUDA libraries you don't need without a GPU.

## Local development

Requires Python 3.12+, Node 20+, and `podman` (or `docker`) on your `PATH`.

```bash
make install     # creates target/venv and installs web/node_modules
make test        # runs pytest (api) and vitest (web)
make run-api     # starts the API on http://localhost:8080 (reload)
make run-web     # starts the Vite dev server on http://localhost:5173
                 # (proxies /api to http://localhost:8080 by default)
```

All local `make` run/build artifacts — the Python venv, the local SQLite
database, local photo storage (`make run-api`'s default
`LUMINARY_PHOTOS_URI`/`LUMINARY_DATABASE_PATH`), and the pytest cache — live
under `target/` at the repo root. `make clean` removes it (along with
`web/node_modules` and `web/dist`, which stay in `web/` since the container
build expects them there).

See `make help` for all targets.

## Building container images

```bash
make build                       # builds luminary-api and luminary-web images
make build IMAGE_REGISTRY=quay.io/myorg TAG=1.0.0
make push                        # pushes both images to IMAGE_REGISTRY
```

`CONTAINER_ENGINE` defaults to `podman`; override with `make build CONTAINER_ENGINE=docker`.

The web image is a multi-stage build: Node builds the static bundle, then an
unprivileged nginx image serves it and reverse-proxies `/api/*` to
`API_PROXY_TARGET` (set via an environment variable at container start,
templated into the nginx config with `envsubst`). This lets the same image
be pointed at any API service URL without rebuilding.

## Deploying to OpenShift

The Helm chart in `deploy/helm/luminary` deploys both components:

- a `Deployment` + `Service` + `Route` for the API, with a `PersistentVolumeClaim`
  for photo storage
- a `Deployment` + `Service` + `Route` for the web app, configured to proxy to
  the API's in-cluster Service

```bash
helm lint deploy/helm/luminary
helm template luminary deploy/helm/luminary
helm upgrade --install luminary deploy/helm/luminary \
  --namespace luminary --create-namespace \
  --set api.image.repository=quay.io/myorg/luminary-api \
  --set api.image.tag=1.0.0 \
  --set web.image.repository=quay.io/myorg/luminary-web \
  --set web.image.tag=1.0.0
```

### GitOps via ArgoCD

`deploy/argo-app.yaml` defines an ArgoCD `Application` that tracks this
repository's `deploy/helm/luminary` path and syncs it into the `luminary`
namespace. Update `spec.source.repoURL` to point at your fork/remote, then:

```bash
oc apply -f deploy/argo-app.yaml
```

With `syncPolicy.automated` enabled, ArgoCD will keep the cluster in sync with
this repository and self-heal any drift.

## Testing

- **api**: `pytest` with FastAPI's `TestClient`, exercising sync/list/
  metadata/thumbnail/original/delete/chat flows against a temporary photos
  directory and SQLite database per test (`make test-api`). Chat tests mock
  the `openai` client and the `chat.ask`/`chat._ask_florence` boundary
  rather than hitting a real endpoint or loading real model weights;
  `torch`/`transformers` aren't installed in CI/dev by default, so the
  Florence-2 "not configured" error path is exercised for real rather than
  mocked.
- **web**: `vitest` + `@testing-library/react`, mocking the API client to
  test loading, empty, populated, and error states (`make test-web`).

## Known limitations

- Dev-only JS tooling (`vite`/`vitest`'s bundled `esbuild`) currently has a
  handful of advisories affecting the local dev server; none of it ships in
  the production nginx image, which only contains the built static assets.
  Run `npm audit` in `web/` periodically and upgrade when a non-breaking fix
  lands.
- Photo metadata is stored in a single SQLite file; this is fine for a
  single-replica deployment but is not meant for `api.replicaCount > 1`
  without moving to a shared database.
- The Florence-2 chat backend isn't installed or exercised in this repo's
  own dev/test environment (`torch`/`transformers` are multi-GB) — it's
  implemented and unit-tested against the documented boundary
  (`chat._load_florence`/`chat._ask_florence`), but hasn't been run
  end-to-end against real model weights here. Worth a manual smoke test
  with `LUMINARY_FLORENCE_DEVICE` set appropriately before relying on it.

## CI

`deploy/Jenkinsfile` is a Jenkins pipeline that checks out this repository,
builds the `luminary-api` and `luminary-web` container images from their
respective `Containerfile`s, archives each as a build artifact, then loads,
tags, and pushes both to `registry.home.glroland.com/luminary/`.
