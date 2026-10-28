# Luminary

A simple photo management application: upload photos, browse them in a gallery,
view full-size, and delete them. Built as two independently deployable
components — a React single-page app and a Python API — each shipped as its
own container image and deployed together via Helm/ArgoCD on OpenShift.

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
  and files. Metadata lives in SQLite; original images and generated
  thumbnails live on disk under a configurable data directory (backed by a
  PersistentVolumeClaim in the cluster).
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
```

## API

| Method | Path                       | Description                        |
|--------|----------------------------|-------------------------------------|
| GET    | `/api/health`              | Liveness/readiness check            |
| GET    | `/api/photos`               | List photo metadata                 |
| POST   | `/api/photos`               | Upload a photo (`multipart/form-data`, field `file`) |
| GET    | `/api/photos/{id}`          | Get metadata for one photo          |
| GET    | `/api/photos/{id}/thumbnail`| Thumbnail JPEG                      |
| GET    | `/api/photos/{id}/original` | Original uploaded file              |
| DELETE | `/api/photos/{id}`          | Delete a photo and its files        |

Configuration (environment variables):

| Variable                  | Default            | Purpose                              |
|----------------------------|---------------------|---------------------------------------|
| `LUMINARY_DATA_DIR`        | `/data/photos`      | Root directory for originals/thumbnails |
| `LUMINARY_DATABASE_PATH`   | `/data/photos.db`   | SQLite database file                  |
| `LUMINARY_CORS_ORIGINS`    | `*`                 | Comma-separated list of allowed origins |
| `PORT`                     | `8080`              | Port uvicorn listens on                |

## Local development

Requires Python 3.12+, Node 20+, and `podman` (or `docker`) on your `PATH`.

```bash
make install     # creates api/.venv and installs web/node_modules
make test        # runs pytest (api) and vitest (web)
make run-api     # starts the API on http://localhost:8080 (reload)
make run-web     # starts the Vite dev server on http://localhost:5173
                 # (proxies /api to http://localhost:8080 by default)
```

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

- **api**: `pytest` with FastAPI's `TestClient`, exercising upload/list/
  metadata/thumbnail/original/delete flows against a temporary data
  directory and SQLite database per test (`make test-api`).
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

## CI

`deploy/Jenkinsfile` is a Jenkins pipeline that checks out this repository,
builds the `luminary-api` and `luminary-web` container images from their
respective `Containerfile`s, archives each as a build artifact, then loads,
tags, and pushes both to `registry.home.glroland.com/luminary/`.
