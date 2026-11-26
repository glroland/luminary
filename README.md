# Luminary

A simple photo browser: index an existing photo library, browse it in a
gallery, view full-size. Built as two independently deployable components —
a React single-page app and a Python API — each shipped as its own container
image and deployed together via Helm/ArgoCD on OpenShift.

**The API never writes to, modifies, or deletes anything on the configured
photos backend, under any circumstance.** It only reads from it (to list,
thumbnail, and serve originals) and indexes what it finds into its own
Postgres database. There is no upload endpoint that writes a file there,
and deleting a photo through the API only removes it from the index — it
never touches the source file. See "Data safety" below.

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
                                 │   - original + thumbnail  │──┐
                                 │     files on disk (PVC)   │  │
                                 └──────────────────────────┘  │
                                               │                │
                                               ▼                │
                                 ┌──────────────────────────┐  │
                                 │  Postgres (external,     │◀─┘
                                 │  not part of this chart) │
                                 │   - photo metadata +     │
                                 │     thumbnails            │
                                 └──────────────────────────┘
```

- **web** — a React single-page app (built with Vite). In production it is
  served as a static bundle by an unprivileged nginx container, which also
  reverse-proxies `/api/*` to the API service so the browser only ever talks
  to one origin.
- **api** — a FastAPI service exposing a small REST API for photo metadata
  and files. It indexes an existing, externally-managed photo library (read
  only — see "Data safety") into Postgres, which also holds generated
  thumbnails; the library itself can be a local path, an NFS mount (backed
  by a PersistentVolumeClaim in the cluster), S3, SMB, or read-only HTTP(S).
  The Postgres server is remote and managed independently of this repo's
  Helm chart/ArgoCD app — see "Database" below.
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

db/                   SQL to provision/tear down the Postgres database +
                      role the API uses -- see "Database" below

deploy/
  helm/luminary/      Helm chart deploying both components to OpenShift
  argo-app.yaml       ArgoCD Application pointing at the Helm chart
  Jenkinsfile         Jenkins pipeline: build, package, and push both images

Makefile             install / test / build / deploy / db helpers
target/               (generated, gitignored) local venv and pytest cache --
                      see "Local development"
```

## API

| Method | Path                       | Description                        |
|--------|----------------------------|-------------------------------------|
| GET    | `/api/health`              | Liveness/readiness check            |
| GET    | `/api/photos`               | List indexed photo metadata         |
| POST   | `/api/photos`               | Always `403` — uploads are disabled; see "Data safety" |
| GET    | `/api/photos/{id}`          | Get metadata for one photo          |
| GET    | `/api/photos/{id}/thumbnail`| Thumbnail JPEG (served from Postgres) |
| GET    | `/api/photos/{id}/original` | Original file, streamed from the photos backend |
| DELETE | `/api/photos/{id}`          | Remove a photo from the index only — does not touch the source file |
| POST   | `/api/sync`                 | Start a background reconciliation of the Postgres index against the configured photos backend (insert rows for new files, update rows for changed files, delete rows for files no longer present) |
| GET    | `/api/sync/status`          | Progress of the current/last sync: `{status, processed, total, inserted, updated, deleted, failed, error}` |
| POST   | `/api/chat`                 | Chat with no photo open — same request/response as below; this is how the library is searched |
| POST   | `/api/photos/{id}/chat`     | Chat with a photo open — `{message, history, search_list}` → a streamed `application/x-ndjson` body of events (tool calls as they happen, the reply, boxes, search results); see "Chat and search" below |

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
- `DELETE /api/photos/{id}` only deletes the Postgres row for that photo.
  If the source file is still there, the next sync re-indexes it.
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
| `LUMINARY_DATABASE_HOST`   | `localhost`         | Postgres host — see "Database" below  |
| `LUMINARY_DATABASE_PORT`   | `5432`              | Postgres port                          |
| `LUMINARY_DATABASE_NAME`   | `luminary`          | Postgres database name                |
| `LUMINARY_DATABASE_USER`   | `luminary`          | Postgres role                          |
| `LUMINARY_DATABASE_PASSWORD` | *(none — required)* | Postgres role's password          |
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

### Database

Photo metadata and generated thumbnails live in Postgres — a server that
already exists and is managed independently of this repo's Helm
chart/ArgoCD app (it's never stood up by `make build`/`helm install`, and
there's no PVC for it). Point `LUMINARY_DATABASE_HOST`/`_PORT`/`_NAME`/
`_USER`/`_PASSWORD` at it; the API connects with those on every request (no
connection pooling), same as it does to the photos backend.

There are three databases, typically all on the same server. `luminary-dev`
and `luminary-unittest` always share the one `luminary` role; prod's own
database can share it too, or use its own distinct role (see `make
db-create` below):

| Database            | Used by                                  |
|-----------------------|-------------------------------------------|
| `luminary-dev`        | `make run-api` (local development)       |
| `luminary-unittest`   | `make test-api` (truncated between every test — see "Testing" below) |
| *(whatever you pick)* | Prod — named in the `database` key of the Secret `values.yaml`'s `api.env.databaseCredentialsSecret` points at, used by the Helm deployment |

`db/` holds the SQL to provision and tear them down:

| File                          | Run as                     | Purpose                                 |
|--------------------------------|-----------------------------|------------------------------------------|
| `db/create_db_and_user.sql`   | a Postgres superuser, against the `postgres` maintenance DB | Creates one database + the shared role, if it doesn't exist yet (idempotent) — run once per database |
| `db/create_extensions.sql`   | a Postgres superuser, against the *target* database | Installs the `pgvector` extension (idempotent) — needed for `photo_embeddings.embedding`/similarity search (see "Similarity search" below); run once per database, after `create_db_and_user.sql` and before `create_tables.sql` |
| `db/create_tables.sql`        | the app role, against one app database | Creates `photos`/`faces` in that database (idempotent; also run automatically on every API startup by `database.init_db()`) |
| `db/drop_database.sql`       | a Postgres superuser, against the `postgres` maintenance DB | Drops one database (destructive) — run once per database, **before** `drop_role.sql` |
| `db/drop_role.sql`           | a Postgres superuser, against the `postgres` maintenance DB | Drops the shared role (destructive) — Postgres refuses this while the role still owns any database |
| `db/update.sql`               | the app role, against one app database | Staging area for the next incremental schema change (`ALTER TABLE`/`CREATE TABLE IF NOT EXISTS`/etc.) — applied by `make db-update` without a full drop/rebuild, which empties this file once it succeeds against both `luminary-dev` and `luminary-unittest`; currently holds the `faces.tag_source` manual-tag backfill and the `photos.location_name` column addition, for any database (e.g. prod's own, provisioned separately — see below) that hasn't had the current `create_tables.sql` applied to it yet |

See [`docs/database-schema.md`](docs/database-schema.md) for the `photos`/
`faces` table/column reference.

```bash
make db LUMINARY_DATABASE_ADMIN_PASSWORD=<superuser password>       # create luminary-dev + luminary-unittest
make db-recreate LUMINARY_DATABASE_ADMIN_PASSWORD=<superuser password>  # drop + recreate, destructive
make db-drop LUMINARY_DATABASE_ADMIN_PASSWORD=<superuser password>  # drop only
make db-update                                                      # apply db/update.sql in place, no drop/rebuild
```

`db`/`db-recreate`/`db-drop`/`db-update` only ever touch `luminary-dev`/
`luminary-unittest` (`LUMINARY_DATABASE_NAME`/`LUMINARY_TEST_DATABASE_NAME`).
Prod's own database is provisioned separately, once, with `make db-create`,
which takes the database name/role as input instead of assuming the
shared dev `luminary` role:

```bash
make db-create \
  LUMINARY_CREATE_DB_NAME=luminary-prod \
  LUMINARY_CREATE_DB_USER=luminary-prod \
  LUMINARY_CREATE_DB_PASSWORD=<password> \
  LUMINARY_DATABASE_ADMIN_PASSWORD=<superuser password> \
  LUMINARY_DATABASE_HOST=<prod postgres host>   # defaults to LUMINARY_DATABASE_HOST
```

`LUMINARY_CREATE_DB_NAME`/`_USER`/`_PASSWORD` have no defaults — `db-create`
refuses to run without all three, same reasoning as
`LUMINARY_DATABASE_ADMIN_PASSWORD` having none. It's the Makefile wrapper
around directly invoking `db/create_db_and_user.sql`/`db/create_tables.sql`
(see their own header comments for the equivalent raw `psql` invocations).
Whatever name/user/password you pick must then go into the `database`/
`username`/`password` keys of the Secret named by `values.yaml`'s
`api.env.databaseCredentialsSecret` (see `deploy/create-secrets.sh`, which
prompts for all three) for the Helm deployment to actually connect with
them.

`deploy/db-rebuild-prod.sh` is the **destructive** sibling of `db-create`
above — a drop+recreate from scratch, for when you want to rebuild prod's
database rather than just provision it the first time. Lives alongside
`deploy/create-secrets.sh` rather than under `db/` since, like that
script, it's an interactive OpenShift-deployment-adjacent operational
tool rather than raw SQL meant to be run/read directly — it drives the
same files under `db/` (`../db/*.sql` relative to its own location) via
plain `psql` instead. Run it directly (no Makefile variables to export up
front):

```bash
deploy/db-rebuild-prod.sh
```

It prompts for the Postgres host/port/admin credentials and the target
database name/username/password, defaulting the name and username both to
`luminary-prod` (press enter to accept) — the same name used throughout
this section and in `deploy/create-secrets.sh`'s own defaults. It requires
typing the database name back as a confirmation before dropping anything,
then runs `drop_database.sql`/`drop_role.sql` followed by
`create_db_and_user.sql`/`create_extensions.sql`/`create_tables.sql`
against it — same three provisioning files `db-create` runs, just driven
directly instead of via Makefile variables. Update
`luminary-db-credentials` (`deploy/create-secrets.sh`) afterward if the
name/username/password changed from what's currently deployed.

`LUMINARY_DATABASE_ADMIN_USER`/`_PASSWORD` (default user `postgres`, no
default password) authenticate only for `db`/`db-recreate`/`db-drop`/
`db-create` — the running API never uses them, and neither does `db-update`, which connects
as the app role instead (it only ever alters objects inside a database
that role already owns, never roles/database ownership itself).
`db-update` also only touches `luminary-dev`/`luminary-unittest` — apply
`db/update.sql` to prod's own database by hand (see that file's own header
comment for the `psql` invocation), the same "separately, not by these
targets" split as provisioning. There's no migrations framework (see
`database.py`'s module docs): a schema change means updating
`database.py`'s `init_db()`, `db/create_tables.sql`, *and* `db/update.sql`
together — the first two so a brand-new database comes up fully current,
the third so an already-provisioned one can be patched up to match
without a full rebuild.

### Chat and search

The floating chat talks to any OpenAI-*compatible* chat endpoint, via the
`openai` Python SDK. `LUMINARY_OPENAI_BASE_URL` is never assumed to be
`api.openai.com` — point it at a self-hosted server (vLLM, Ollama, LM
Studio, etc.) if that's what you're running. With a photo open
(`POST /api/photos/{id}/chat`) the image is base64-encoded and attached to
the current message on every call, so the model can answer questions about
it; with none open (`POST /api/chat`) the chat is about the library as a
whole.

**The chat is also the app's search.** There is no search box: ask the
chat to find photos and the model picks one of five search tools, passing
the search text itself.

| Tool                         | Finds photos by                                                                 |
|-------------------------------|----------------------------------------------------------------------------------|
| `search_photos_by_filename`  | Text in the file name or folder path (case-insensitive; every word must appear) |
| `search_photos_semantic`     | Meaning — photos whose description is similar to the query (see "Similarity search" below). Optional `match_level`: `loose` (the default, similarity ≥ 0.3), `moderate` (0.5), `strict` (0.7), `very_strict` (0.85) |
| `search_photos_by_text`      | Plain text match across everything known about a photo: file name and path, description, Florence-2 caption, location, names of people tagged in it, and tags (every word must appear somewhere) |
| `search_photos_by_individual`| Person — photos with a face tagged to the named individual, manually or automatically |
| `search_photos_by_tag`       | Tag — photos carrying the named tag |

For the last two, a name matches exactly (ignoring case) if it can, and
otherwise by any name containing it — "alice" finds "Alice Smith" unless
someone is called just "Alice". Two more tools, `list_individuals` and
`list_tags`, give the model every person and tag with its photo count, so
it can answer "who's in my library?" or look up the right name first;
those only produce text, not gallery results.

**The search list.** Matches are kept out of the model's context, so a
model with a small context window isn't handed hundreds of file names. A
search puts its matches in a *search list* of `{id, filename, score}`
entries (score 0–1; a semantic match scores its similarity, any other
match 1), and the model is told only how many photos the list holds, its
highest, median and lowest score, and the names of the three best. Every
search tool takes a `mode`: `new` (the default) replaces the list,
`filter` keeps only the photos already in it that also match, `add`
merges the new matches in. Two further tools work on the list itself:

| Tool                   | Does                                                                              |
|------------------------|-----------------------------------------------------------------------------------|
| `search_list_stats`    | Reports the count and highest/median/lowest score; given a `score`, also how many photos are at or above it and how many below |
| `truncate_search_list` | Removes every photo scoring below `min_score`                                     |

The response's `search_results` carries the list, best score first (`[]`
if a first search matched nothing, `null` if the list wasn't changed).
The web UI closes any open photo and shows them in the gallery under the
sidebar's **Search Results** row, each tile with its match score as a
percentage in the bottom-right corner; that row brings the last search
back after browsing elsewhere. The UI sends the list back as the
request's `search_list` with each message, which is how a follow-up
("only the best ones", "just the ones with Alice") works on the previous
search. A search returns at most 500 photos.

**Object search.** While a photo is open the model has one more tool,
`locate_object`, for questions like "where's the dog?" or "is there a stop
sign?". It passes the phrase to search for; the API runs it through a local
[Florence-2](https://huggingface.co/microsoft/Florence-2-base)
vision-language model (`<CAPTION_TO_PHRASE_GROUNDING>`, in-process via
`torch`/`transformers`, loaded lazily on first use), hands the bounding
boxes found back to the chat model to word its answer, and returns them in
the response's `boxes` so the web UI can highlight them on the photo.
Florence-2 isn't a conversational model — it only locates a phrase — which
is why it's a tool of the chat model rather than something you pick. It's
only offered when `torch`/`transformers` are installed.

**The chat endpoint has to support tool calling** — e.g. vLLM needs
`--enable-auto-tool-choice` and a `--tool-call-parser` for the model. If
yours rejects requests that carry `tools`, set `LUMINARY_CHAT_TOOLS=false`:
chat still answers questions about the open photo, but there is then no
way to search.

**The response is a stream of events.** Both chat endpoints respond with
`application/x-ndjson`, one JSON object per line, sent as it happens:

- `{"tool": {"index", "name", "arguments", "status"}}` when the model calls
  a tool (`status: "running"`) and again when that call has finished
  (`"done"`, same `index`). The chat window shows each call as a line of
  its own — "Searching for photos of “beach”" — with a spinner until it
  finishes;
- `{"text": ...}` for the reply: in pieces as the model writes it with
  `LUMINARY_OPENAI_STREAMING` on, otherwise (the default) as one event once
  it is complete;
- `{"boxes": [...]}` when an object search found something, and
  `{"search_results": [...]}` when the search list changed.

A failure before the first event (not configured, the model endpoint
unreachable) is still an ordinary `503`/`502` JSON error; one after the
stream has started is reported in it, as a final `[chat backend failed]`
text event.

**Tracing.** Set `MLFLOW_TRACKING_URI` and every chat turn is recorded as
an [MLflow](https://mlflow.org/docs/latest/genai/tracing/) trace — the turn,
each call to the model (messages, reply, token counts when the server
reports them) and each tool call (arguments, result) — as is every
description rewrite ("Description enrichment" below). The open photo's
bytes are left out of the trace. Tracing can't break the app: if MLflow
can't be reached at startup an error is logged, the app runs untraced and
the connection is retried in the background (30s, doubling to 10 minutes);
if it goes away later, traces are dropped with a log line. These are
MLflow's own variables, read by MLflow itself:

| Variable                 | Default    | Purpose                                 |
|--------------------------|------------|-----------------------------------------|
| `MLFLOW_TRACKING_URI`    | *(none)*   | Tracking server URL; unset = no tracing (Helm: `api.env.mlflowTrackingUri`) |
| `MLFLOW_EXPERIMENT_NAME` | `luminary` | Experiment the traces are filed under (Helm: `api.env.mlflowExperimentName`) |
| `MLFLOW_WORKSPACE`       | *(none)*   | MLflow workspace, where the server has them (Helm: `api.env.mlflowWorkspace`) |
| `MLFLOW_TRACKING_AUTH`   | *(none)*   | `kubernetes-namespaced` to authenticate as the pod's service account (Helm: `api.env.mlflowTrackingAuth`; OpenShift AI also needs the RoleBinding `api.mlflowIntegration.serviceAccounts` creates) |
| `MLFLOW_TRACKING_TOKEN`  | *(none)*   | Bearer token, for running locally (e.g. `oc whoami --show-token`) |

| Variable                   | Default                     | Purpose                              |
|-----------------------------|------------------------------|---------------------------------------|
| `LUMINARY_OPENAI_API_KEY`  | *(none)*                    | Required                              |
| `LUMINARY_OPENAI_BASE_URL` | *(none — SDK default)*      | OpenAI-compatible endpoint URL        |
| `LUMINARY_OPENAI_MODEL`    | *(none)*                    | Required — the model name as your server calls it |
| `LUMINARY_OPENAI_STREAMING`| `false`                     | Send the reply text token-by-token instead of all at once (tool calls are reported live either way) |
| `LUMINARY_CHAT_TOOLS`      | `true`                      | Offer the chat model its tools (photo search, object search); set `false` if the endpoint doesn't support tool calling (Helm: `api.env.chatTools`) |
| `LUMINARY_OPENAI_VERIFY_SSL`| `true`                     | Validate the endpoint's TLS certificate; set `false` only for a trusted self-signed/internal-CA endpoint (Helm: `api.env.openaiVerifySsl`) |
| `LUMINARY_FLORENCE_MODEL`  | `microsoft/Florence-2-base` | Any Florence-2 checkpoint (object search, and captions at index time) |
| `LUMINARY_FLORENCE_DEVICE` | `cpu`                       | `cpu` or `cuda`                       |

Chat that's missing required config returns `503` with a message
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

### Face detection

`POST /api/sync` detects faces in each indexed photo via facenet-pytorch's
MTCNN (see `face_detection.py`'s module docstring) — a separate step from
the manual/automatic face *tagging* covered elsewhere in this README. Two
tunables:

| Variable                          | Default | Purpose                              |
|------------------------------------|---------|---------------------------------------|
| `LUMINARY_FACE_DETECTION_THRESHOLD`| `0.8`   | Confidence MTCNN must clear to report a detected face — lower catches more faces (partial/angled/small, or in a crowded photo) at the cost of more false positives; no single right value across different libraries, tune against your own |
| `LUMINARY_PET_DETECTION`           | `1`     | Also detect cats and dogs while indexing (`pet_detection.py`, a COCO Faster R-CNN) so they can be tagged like people. `0` skips the extra model pass entirely; human face detection never depends on it |
| `LUMINARY_PET_DETECTION_THRESHOLD` | `0.6`   | Minimum detector score for a cat/dog box — lower finds more partly-hidden animals at the cost of false positives; no verified-correct value, tune against your own library |
| `LUMINARY_FACE_MIN_SIZE`           | `150`   | Smallest face, in pixels of MTCNN's internally-resized copy of the image (not the original photo's resolution), it will report — lower catches smaller/more distant faces at the cost of more false positives and slower detection; facenet-pytorch's own MTCNN default (`20`) was confirmed too permissive in practice (spurious small-box detections) |
| `LUMINARY_PET_EMBEDDING_MODEL`     | `facebook/dinov2-small` | Image-embedding model that tells one pet from another (DINOv2 via `transformers`, ~90 MB; must be in the container's model cache) |
| `LUMINARY_PET_MATCH_THRESHOLD`     | `0.8`   | Cosine similarity at or above which a pet box is auto-tagged as a pet you tagged by hand. A different scale from the face one — DINOv2 scores run high (two unrelated test images already scored 0.79), so it starts strict; a too-loose value writes wrong tags, a too-strict one only matches nothing, and the log names the closest unmatched score to tune from. Not a verified value |
| `LUMINARY_PET_REJECT_THRESHOLD`    | `0.92`  | Pet counterpart of the face reject threshold below: similarity to a pet box you unmatched at or above which it won't be tagged as that pet again |
| `LUMINARY_FACE_REJECT_THRESHOLD`   | `0.7`   | Cosine similarity to a face you previously untagged (an auto-match you removed) at or above which "Refresh Faces" won't tag a candidate as that same person again; no verified-correct value, tune against the veto lines in the log |

For local testing, just set them in your shell before running `make
run-api` or `make test-api` (`config.py` reads both lazily, same as every
other `LUMINARY_*` setting, so neither is hardcoded into either Makefile
target):

```bash
LUMINARY_FACE_DETECTION_THRESHOLD=0.5 LUMINARY_FACE_MIN_SIZE=40 make run-api
```

For a deployed cluster, they're `api.env.faceDetectionThreshold`/
`api.env.faceMinSize` in the Helm chart's `values.yaml` (see
`deploy/helm/luminary/values.yaml`) — either edit them there or pass
`--set api.env.faceDetectionThreshold=0.5 --set api.env.faceMinSize=40` to
`helm upgrade`.

### Background job concurrency

`sync.py`'s indexing (face detection included) and `recognition.py`'s
"Refresh Faces" each fan files/photos out across a `ThreadPoolExecutor`
rather than processing one at a time — how many at once is
`LUMINARY_MAX_CONCURRENCY` (default `8`, the original hardcoded value for
both — see `config.get_max_concurrency()`'s docstring). Unlike the
settings above, it's read once at process start, not re-read per sync/
refresh run, since the executor's pool size can only be chosen once.

`make run-api` defaults it lower, to `4` (set in the Makefile, not
`config.py`, so it only affects local runs) — a laptop running `make
run-api` is typically sharing CPU with everything else you're doing,
unlike a dedicated k8s pod. Override with `make run-api
LUMINARY_MAX_CONCURRENCY=8`. For a deployed cluster it's
`api.env.maxConcurrency` in the Helm chart's `values.yaml`, defaulting to
`8` — edit it there or pass `--set api.env.maxConcurrency=4` to `helm
upgrade`.

### Reverse geocoding

Alongside the raw `latitude`/`longitude` decoded from a photo's EXIF GPS
tags, sync also stores a short, human-readable description of the same
coordinates in `photos.location_name` — e.g. `"Boone, North Carolina"`,
`"Grandfather Mountain State Park, Blowing Rock, North Carolina"`, or
`"Target, 123 North Point Pkwy, Alpharetta, Georgia"` if that exact spot
is mapped as a named business/street in OpenStreetMap — via
[OpenStreetMap's Nominatim](https://nominatim.org/) reverse-geocoding API
(free, no API key — the same OpenStreetMap service `PhotoMap.jsx` already
depends on for map tiles). This is best-effort: a lookup failure (network
error, timeout, or no GPS data on the photo at all) just leaves
`location_name` `null`, the same as a missing thumbnail or description —
it never fails a sync.

| Variable                    | Default                                     | Purpose                              |
|-------------------------------|------------------------------------------------|-----------------------------------------|
| `LUMINARY_GEOCODE_BASE_URL`  | `https://nominatim.openstreetmap.org/reverse` | Nominatim-compatible reverse-geocoding endpoint |
| `LUMINARY_GEOCODE_USER_AGENT`| `luminary-photo-browser/1.0`                | Sent as the `User-Agent` header — [Nominatim's usage policy](https://operations.osmfoundation.org/policies/nominatim/) requires one identifying the calling application |
| `LUMINARY_GEOCODE_ZOOM`      | `18`                                         | Which OSM feature layer the lookup resolves to (low = broad administrative area, high up to `18` = individual building/named business) — no single right value across very different photo subjects; tune against your own library |

The public Nominatim instance's usage policy caps requests at **one per
second**; `geocode.py` enforces that globally across sync's concurrent
worker threads, so it becomes the long pole of a sync for a GPS-heavy
library. Point `LUMINARY_GEOCODE_BASE_URL` at a self-hosted Nominatim
instance to remove that cap (or avoid depending on the public instance at
all, e.g. for an airgapped deployment).

Like `latitude`/`longitude` themselves, `location_name` is only
(re)computed when a photo is newly indexed or its file content changes
during sync — there's no automatic backfill for photos already indexed
before a geocoding-related setting (`LUMINARY_GEOCODE_ZOOM`, or the set of
address fields `geocode.py` looks at) changed. The management page's
**Refresh Locations** button (`POST /api/locations/refresh`, polled via
`GET /api/locations/refresh/status`) re-geocodes every photo that already
has GPS coordinates, picking up that new result — same background-job
shape as "Refresh Faces"/"Refresh Descriptions" (own thread, progress
polling, `idle`/`running`/`completed`/`error`), and subject to the same
one-request-per-second cap against the public Nominatim instance above.

### Trips

The sidebar's **By Trip** section groups photos into trips: runs of photos
taken away from home. Only photos with **both a date and GPS coordinates**
take part — without either there's nothing to place on a timeline or a map,
so such a photo is never put in a trip automatically. Everything is derived
from data already in Postgres; the photos backend is never touched.

How it decides (`trip_detection.py`, pure and unit-tested; `trips.py` stores
the result):

1. **Home** is worked out from where the most distinct days are spent in each
   calendar year, so a move gives two dated homes. If you've entered or
   confirmed a home, that wins outright.
2. A photo farther than `LUMINARY_TRIP_HOME_RADIUS_KM` from the home in force
   on its date is *away*.
3. Away photos form a trip until more than `LUMINARY_TRIP_GAP_DAYS` pass with
   none, or a whole day is spent at home.
4. Candidates under `LUMINARY_TRIP_MIN_PHOTOS` photos or `LUMINARY_TRIP_MIN_HOURS`
   hours are dropped. One calendar day is a **day outing** (hidden in the
   sidebar until "Show day outings" is ticked); two or more are a **trip**.

Selecting a trip shows its photos in date order, grouped by day, under a map:
one numbered marker per stop (photos within `LUMINARY_TRIP_STOP_RADIUS_KM` of
each other), a dashed line in the order they were visited, and a home marker.
Click a stop to narrow the gallery to it. Times are treated as the wall clock
where the photo was taken — no time zone is worked out, since every threshold
is in days.

**Editing.** Detection is a starting point; what you change is kept. In a
trip's header: **Rename** (or go back to the generated name), **Merge with…**
another trip (the earlier one keeps its place), **Split…** at a date, and
**Dismiss** (hidden, and never detected again until restored from the Manage
page). In the gallery, right-click photos to **Add to trip…** (any photo, with
or without a date or GPS; it moves them out of another trip), and inside a trip
**Remove from this trip** (detection won't put them back) or **Use as the
trip's cover**. The Manage page's **Trips** section also makes a trip from a
date range. A rebuild only ever adds new trips and extends existing ones with
photos that are in no trip: it never moves a photo between trips, merges,
splits, or brings back what you dismissed.

**Home.** The Manage page shows the detected home(s) with an "Is this home?"
prompt until confirmed. Confirm it, edit it (coordinates, radius, and the dates
you lived there — add a second one when you move), or enter your own; once
you have, only those are used. Removing the last one lets detection take over
again. Changing a home rebuilds trips.

Trips are rebuilt after every sync and Refresh Locations, and on demand with
the management page's **Rebuild Trips** button (`POST /api/trips/refresh`,
polled via `GET /api/trips/refresh/status`). Names use the place names from
geocoding, so they get better after **Refresh Locations**.

| Variable                          | Default | Purpose                                                                 |
|-----------------------------------|---------|-------------------------------------------------------------------------|
| `LUMINARY_TRIP_HOME_RADIUS_KM`    | `50`    | Distance from home beyond which a photo counts as away                  |
| `LUMINARY_TRIP_GAP_DAYS`          | `2`     | Days with no away photo that end a trip                                 |
| `LUMINARY_TRIP_MIN_PHOTOS`        | `10`    | Fewest photos for a trip/outing                                         |
| `LUMINARY_TRIP_MIN_HOURS`         | `4`     | Shortest span (first to last photo)                                     |
| `LUMINARY_TRIP_HOME_MIN_DAYS`     | `30`    | Distinct days in a year a place needs to be detected as home            |
| `LUMINARY_TRIP_STOP_RADIUS_KM`    | `2`     | Photos this close together are one stop on the map                      |

Like the face-match threshold, none of these has a verified-correct value;
tune them against your own library.

### Pets

Cats and dogs are detected while indexing (`pet_detection.py`; turn it off
with `LUMINARY_PET_DETECTION=0`) and tagged like people — add a *pet* in the
photo viewer or on the Manage page, then right-click a cyan box. Photos
indexed before this existed aren't re-scanned by sync; the management page's
**Detect Pets** button (`POST /api/pets/refresh`) backfills them. It only
adds pet boxes — people and their tags are never touched — and re-running it
does nothing for photos it has already scanned.

Once you've tagged a pet by hand, the same post-sync matching that handles
people (and the Manage page's face refresh) auto-tags other boxes of that
pet, marked "auto" like people, and unmatching one is remembered the same
way. Pets are matched with their own model (DINOv2) and their own
thresholds (`LUMINARY_PET_MATCH_THRESHOLD`), never against people. If the
pet model can't load, pets are skipped and people are still matched.

### Description enrichment

The description shown in the UI (`photos.description`) isn't Florence-2's
raw caption — that's stored separately, in `photos.vlm_description`, and
never shown directly. Instead, `enrich.py` has a general-purpose LLM (any
OpenAI-*compatible* chat endpoint — the **same** `LUMINARY_OPENAI_*`
config the "openai" photo chat backend above uses, so no separate setup is
needed if that's already configured) rewrite whichever facts are on hand
for a photo — its `vlm_description`, the names of any individuals tagged
in it, its `location_name`, and its filename — into one short,
natural-language description.

This is recomputed every time any of those facts can change for a photo,
not just once at index time: a sync that (re)generates `vlm_description`,
tagging or untagging a face, "Refresh Faces" auto-matching a face, or
deleting a tagged individual. Each of those calls is synchronous (an LLM
call over text only, no image — cheap enough to make inline, the same way
`POST /api/photos/{id}/chat` already makes its own openai call
synchronously) and best-effort: if the openai backend isn't configured, or
a call fails, `description` is simply left as whatever it already was
(`null` if it's never succeeded yet) rather than the tag/untag/sync/refresh
failing.

No separate configuration beyond `LUMINARY_OPENAI_API_KEY`/`_MODEL`/
`_BASE_URL` (see "Photo chat" above) — if those aren't set, description
enrichment just never runs, the same as the openai chat backend itself.

The management page's **Refresh Descriptions** button
(`POST /api/descriptions/refresh`, polled via
`GET /api/descriptions/refresh/status`) is the bulk equivalent of the
individually-triggered recomputation above: it regenerates `description`
for every photo in the library, not just ones whose underlying facts just
changed. Use it after changing `enrich.py`'s prompt (e.g. wanting more
detail than existing rows were generated with) or after configuring the
`openai` backend for the first time on a library that was synced before
it was available. Same background-job shape as "Refresh Faces"
(own thread, progress polling, `idle`/`running`/`completed`/`error`).

### Similarity search (description embeddings)

`GET /api/photos/{id}/similar` finds other photos whose `description`
*reads* most like this one's, via per-sentence embeddings in the
`photo_embeddings` table and pgvector's cosine-distance operator — unrelated to "Refresh Faces" above,
which matches *faces* by image embedding, not photos by description text.

The chat's `search_photos_semantic` tool (see "Chat and search" above) is
the free-text equivalent: it embeds the query text with the same model as
the stored sentence embeddings, so the two are comparable, and returns the
photos at least as similar as its match level, most-similar first
(`storage.search_photos`). `GET /api/photos/search?q=...&threshold=...`
exposes the same search over plain HTTP; the web UI no longer calls it.

`embeddings.py` embeds with a **local** Hugging Face sentence-embedding
model (`LUMINARY_EMBEDDING_MODEL`, default `BAAI/bge-large-en-v1.5`), run
in-process via `sentence-transformers` — deliberately *not* a call to an
OpenAI-compatible embeddings endpoint. `LUMINARY_OPENAI_*` is for *chat
completions* only (see "Description enrichment" above); a self-hosted
OpenAI-compatible server may expose no embeddings endpoint at all, or one
backed by a different, incompatible model than whatever already generated
the embeddings stored in this database — running the model ourselves
removes that dependency entirely. At 1,024 native dimensions, the column
is declared `vector(1024)` (pgvector's plain type, whose HNSW/IVFFlat
index cap is 2,000 dimensions — no need for `halfvec`'s precision
tradeoff at this size, unlike the previous OpenAI `text-embedding-3-large`
choice). See `embeddings.py`'s module docstring for the full reasoning,
including what to do if you swap in a different embedding model.

A photo's `photo_embeddings` rows (one per description sentence) are regenerated automatically alongside
`description` itself — same trigger list as "Description enrichment"
above (including the "Refresh Descriptions" button), no separate step.
Running "Refresh Descriptions" is also how an already-populated library
picks up a newly-swapped-in `LUMINARY_EMBEDDING_MODEL` — it re-embeds
every photo's existing description text, no separate backfill job.
Best-effort, same semantics as every other derived field in this app: a
failure (missing torch/sentence-transformers, a Hugging Face cache miss)
just leaves it as whatever it already was.

**Reranking.** Semantic search is two-stage: the embedding search above
takes the best `LUMINARY_RERANK_CANDIDATES` photos (default 100) above a
loose floor, then `rerank.py` re-scores each against its whole description
with a local cross-encoder (`LUMINARY_RERANKER_MODEL`, default
`BAAI/bge-reranker-base`; `LUMINARY_RERANKER_DEVICE`). The match level then
applies to that score (0–1, a sigmoid of the model's logit). Set
`LUMINARY_RERANKER_MODEL=""` to turn it off; if the model isn't installed
or cached, search silently keeps the embedding order. The match levels were
chosen for cosine similarity, so tune them with `make eval-search
QUERIES=queries.json` (`api/eval_search.py`: precision/recall/MRR with and
without reranking, and a threshold sweep, over queries you've judged).

**Requires the `pgvector` Postgres extension** — see `db/
create_extensions.sql` and the "Database" section above. Without it,
`database.py`'s `init_db()` logs a warning once at startup and skips
`photo_embeddings.embedding`/its index; every other table/column still works
normally, and `GET /api/photos/{id}/similar` returns an empty list
instead of erroring.

## Local development

Requires Python 3.12+, Node 20+, `podman` (or `docker`), and a reachable
Postgres server (see "Database" above) on your `PATH`/network.

```bash
make install     # creates target/venv, installs web/node_modules, downloads the models into target/model-cache
make db          # provisions luminary-dev + luminary-unittest, role, and tables (first time only)
make test        # runs pytest (api) and vitest (web)
make run-api     # starts the API on http://localhost:8080 (reload)
make run-web     # starts the Vite dev server on http://localhost:5173
                 # (proxies /api to http://localhost:8080 by default)
```

All local `make` run/build artifacts — the Python venv and the pytest
cache — live under `target/` at the repo root. `make clean` removes it
(along with `web/node_modules` and `web/dist`, which stay in `web/` since
the container build expects them there). Photo metadata now lives in
Postgres rather than under `target/` — see "Database" above.

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

The api image's build also downloads and bakes in Florence-2
(`LUMINARY_FLORENCE_MODEL`, same Makefile variable `run-api` uses --
`make build-api LUMINARY_FLORENCE_MODEL=...` passes it through as a
`--build-arg`), facenet-pytorch's vggface2 face-embedding weights
("Refresh Faces"), and the local embedding model behind similarity search
(`LUMINARY_EMBEDDING_MODEL`, same override story — see "Similarity
search" below), so none of the three is ever downloaded at container
startup or on a pod's first real chat/sync/"Refresh Faces"/search
request — `HF_HUB_OFFLINE=1` is set for the running container
specifically to make that a hard guarantee (a mismatched
`LUMINARY_FLORENCE_MODEL`/`LUMINARY_EMBEDDING_MODEL` at deploy time fails
loudly instead of silently fetching a different checkpoint). This does
mean `make build-api`/the Jenkinsfile's equivalent step need network
access to Hugging Face/GitHub during the build, and the resulting image
is correspondingly larger (all three sets of weights, not just the Python
dependencies) -- see `api/Containerfile`'s own comments for the full
reasoning.

Locally, `make install` (its `install-models` step, which runs the same
`api/download_models.py` the image build does) downloads those three sets
of weights into `target/model-cache`, and `make run-api` points
`HF_HOME`/`TORCH_HOME` there with `HF_HUB_OFFLINE=1` — the same setup as the
deployed pod. `make run-api LUMINARY_USE_MODEL_CACHE=0` opts out (tools use
their own defaults and may download). This only caches the *files*; the
model is still loaded into memory once per process on first use.

## Deploying to OpenShift

The Helm chart in `deploy/helm/luminary` deploys both components:

- a `Deployment` + `Service` + `Route` for the API, with a `PersistentVolumeClaim`
  for photo storage
- a `Deployment` + `Service` + `Route` for the web app, configured to proxy to
  the API's in-cluster Service

Before the first install, create the Secrets the chart expects for the
database, the photos backend, and the OpenAI-compatible chat endpoint
(`oc login` first):

```bash
deploy/create-secrets.sh
```

This prompts for the database username/password, the photos-backend
username/password, and the OpenAI API key, then creates
`luminary-db-credentials`, `luminary-photos-credentials`, and
`luminary-openai-credentials` in the `luminary` namespace — the exact names
`deploy/helm/luminary/values.yaml`'s `api.env.databaseCredentialsSecret` /
`api.env.photosCredentialsSecret` / `api.env.openaiApiKeySecret` already
point at. The database username/password must match whatever role
`make db` (see "Database" below) actually provisioned. Skip the photos
and/or OpenAI prompts' secrets only if your photos backend needs no
credentials (local/NFS, S3 via IAM role) and you're not using the `openai`
chat backend — the database secret is always required.

```bash
helm lint deploy/helm/luminary
helm template luminary deploy/helm/luminary
helm upgrade --install luminary deploy/helm/luminary \
  --namespace luminary --create-namespace \
  --set image.repository=quay.io/myorg \
  --set image.tag=1.0.0
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
  directory and the real `luminary-unittest` Postgres database (`make
  test-api` points `LUMINARY_DATABASE_*` at it — see "Database" above) —
  there's no per-test throwaway database; the test fixture truncates
  `photos`/`faces` at the start of every test for isolation instead.
  `luminary-unittest` is separate from `luminary-dev` (what `make run-api`
  uses), so a test run never clears your locally-indexed dev photos. Chat
  tests mock the `openai` client and the
  `chat.ask`/`chat.locate_object` boundary rather than hitting a real
  endpoint or loading real model weights; the Florence-2 "torch/transformers
  not installed" error path is simulated by patching `__import__` rather
  than relying on them being absent from the environment (`make install`
  does pull them in).
- **web**: `vitest` + `@testing-library/react`, mocking the API client to
  test loading, empty, populated, and error states (`make test-web`).

## Known limitations

- Dev-only JS tooling (`vite`/`vitest`'s bundled `esbuild`) currently has a
  handful of advisories affecting the local dev server; none of it ships in
  the production nginx image, which only contains the built static assets.
  Run `npm audit` in `web/` periodically and upgrade when a non-breaking fix
  lands.
- Photo metadata now lives in Postgres, which multiple API replicas could
  share, but `api.replicaCount > 1` still isn't supported: `sync.py`'s
  in-flight progress (`sync.progress`) and background thread are in-memory,
  per-process state, not backed by the database. Each connection is also a
  fresh, unpooled Postgres connection per call — fine at this project's
  scale, but a connection pool would be the first thing to add alongside
  multi-replica support.
- Florence-2 has been smoke-tested end-to-end against real
  `microsoft/Florence-2-base` weights on CPU (both the grounding and plain
  caption paths). Unit tests cover the documented boundary
  (`chat._load_florence`/`chat.locate_object`) with fakes, plus the
  "torch/transformers not installed" path simulated at the import boundary
  (not relied on as ambient environment state, since `make install` does
  pull them in from `requirements.txt`). Pin `transformers==4.49.0` exactly
  as in `requirements.txt` — Florence-2's `trust_remote_code=True` model
  code breaks against newer `transformers` releases (see `chat.py`'s
  comment on that pin).

## CI

`deploy/Jenkinsfile` is a Jenkins pipeline that checks out this repository,
builds the `luminary-api` and `luminary-web` container images from their
respective `Containerfile`s, archives each as a build artifact, then loads,
tags, and pushes both to `registry.home.glroland.com/luminary/`.
