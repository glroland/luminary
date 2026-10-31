CONTAINER_ENGINE ?= podman
IMAGE_REGISTRY   ?= registry.home.glroland.com/luminary
API_IMAGE        ?= $(IMAGE_REGISTRY)/luminary-api
WEB_IMAGE        ?= $(IMAGE_REGISTRY)/luminary-web
TAG              ?= latest

# All local, gitignored run/build artifacts (venv, pytest cache) live under
# here -- `make clean` just removes it. Photo metadata now lives in a
# separate, remote Postgres (see LUMINARY_DATABASE_* below and db/) rather
# than a local file, so this no longer holds a database.
TARGET_DIR  ?= target
VENV_DIR    := $(TARGET_DIR)/venv

# Where run-api looks for/stores photos -- override to point at NFS/S3/SMB/
# HTTP instead, e.g. `make run-api LUMINARY_PHOTOS_URI=s3://my-bucket/photos`.
LUMINARY_PHOTOS_URI ?= "smb://data.home.glroland.com/photo/2019 Spring Break in Boone"

# Postgres connection for photo metadata + thumbnails -- a remote server
# that already exists outside this repo's Helm chart/ArgoCD app (see
# CLAUDE.md); `make db`/`make recreate-db` provision databases+role on it,
# they don't stand up Postgres itself. The same `luminary` role is shared
# across every database (dev, unittest, and prod's own "luminary" -- see
# values.yaml's api.env.database.name, used by the Helm deployment); only
# the database *name* differs per purpose. Override individually, e.g.
# `make run-api LUMINARY_DATABASE_HOST=otherhost`.
LUMINARY_DATABASE_HOST     ?= db.home.glroland.com
LUMINARY_DATABASE_PORT     ?= 5432
LUMINARY_DATABASE_USER     ?= luminary
LUMINARY_DATABASE_PASSWORD ?= luminary

# Database name run-api/db/recreate-db/drop-db use -- local development's
# own database, distinct from the one `make test-api` uses (below) and from
# prod's "luminary" (the Helm deployment's database.name, never this repo's
# to create/drop -- see the README's "Database" section).
LUMINARY_DATABASE_NAME ?= luminary-dev

# Database name test-api uses -- kept separate from LUMINARY_DATABASE_NAME
# so a test run's table truncation (see conftest.py's `client` fixture)
# can't wipe your locally-indexed dev photos.
LUMINARY_TEST_DATABASE_NAME ?= luminary-unittest

# Admin credentials used only by `make db`/`make recreate-db`/`make drop-db`
# to connect to the `postgres` maintenance database and create/drop the
# database+role above -- never used by the running API itself, and never
# defaulted to a real password; supply it explicitly, e.g.
# `make db LUMINARY_DATABASE_ADMIN_PASSWORD=...`.
LUMINARY_DATABASE_ADMIN_USER     ?= postgres
LUMINARY_DATABASE_ADMIN_PASSWORD ?=

# Base URL of the OpenAI-*compatible* endpoint run-api's photo chat uses --
# never assumed to be api.openai.com. Empty means "use the openai SDK's own
# default" (api.openai.com), which is only right if that's really the
# target. Override e.g. `make run-api LUMINARY_OPENAI_BASE_URL=http://localhost:11434/v1`.
LUMINARY_OPENAI_BASE_URL ?= https://maas.apps.ocp.home.glroland.com/sandbox/gemma-4/v1

# Model name as LUMINARY_OPENAI_BASE_URL's server calls it -- matches the
# default base URL above; override alongside it if you point run-api at a
# different endpoint, e.g. `make run-api LUMINARY_OPENAI_BASE_URL=... LUMINARY_OPENAI_MODEL=...`.
LUMINARY_OPENAI_MODEL ?= gemma-4

# Florence-2 checkpoint for the local vision-language chat backend -- loaded
# once per process and cached (chat.py's _florence_model/_florence_processor),
# so changing this requires a full restart of run-api, not just re-running a
# chat request -- `--reload` only restarts on file changes, not env changes.
# Override e.g. `make run-api LUMINARY_FLORENCE_MODEL=microsoft/Florence-2-large`.
LUMINARY_FLORENCE_MODEL ?= microsoft/Florence-2-base

# Env vars for run-api: points at LUMINARY_DATABASE_NAME (the dev database).
LUMINARY_DATABASE_ENV = LUMINARY_DATABASE_HOST=$(LUMINARY_DATABASE_HOST) \
	LUMINARY_DATABASE_PORT=$(LUMINARY_DATABASE_PORT) \
	LUMINARY_DATABASE_NAME=$(LUMINARY_DATABASE_NAME) \
	LUMINARY_DATABASE_USER=$(LUMINARY_DATABASE_USER) \
	LUMINARY_DATABASE_PASSWORD=$(LUMINARY_DATABASE_PASSWORD)

# Env vars for test-api: same host/role, but LUMINARY_TEST_DATABASE_NAME
# (the unittest database) instead.
LUMINARY_TEST_DATABASE_ENV = LUMINARY_DATABASE_HOST=$(LUMINARY_DATABASE_HOST) \
	LUMINARY_DATABASE_PORT=$(LUMINARY_DATABASE_PORT) \
	LUMINARY_DATABASE_NAME=$(LUMINARY_TEST_DATABASE_NAME) \
	LUMINARY_DATABASE_USER=$(LUMINARY_DATABASE_USER) \
	LUMINARY_DATABASE_PASSWORD=$(LUMINARY_DATABASE_PASSWORD)

.PHONY: help install install-api install-web \
        test test-api test-web \
        lint lint-web \
        run-api run-web \
        build build-api build-web \
        push push-api push-web \
        images \
        db recreate-db drop-db \
        helm-lint helm-template \
        clean

help:
	@echo "Available targets:"
	@echo "  install         Install dependencies for both api and web"
	@echo "  test            Run tests for both api and web"
	@echo "  lint            Run lint checks for web"
	@echo "  run-api         Run the API locally with uvicorn (reload)"
	@echo "  run-web         Run the web app locally with the Vite dev server"
	@echo "  build           Build container images for api and web"
	@echo "  push            Push container images for api and web"
	@echo "  db              Create the luminary-dev/luminary-unittest databases, role, and tables (see db/)"
	@echo "  recreate-db     Drop and recreate them"
	@echo "  drop-db         Drop them"
	@echo "  helm-lint       Lint the Helm chart"
	@echo "  helm-template   Render the Helm chart"
	@echo "  clean           Remove local build/test artifacts"

## --- Dependencies -----------------------------------------------------

install: install-api install-web

install-api:
	python3 -m venv $(VENV_DIR)
	$(VENV_DIR)/bin/pip install --upgrade pip
	$(VENV_DIR)/bin/pip install -r api/requirements-dev.txt

install-web:
	cd web && npm install

## --- Tests & lint -------------------------------------------------------

test: test-api test-web

test-api:
	$(LUMINARY_TEST_DATABASE_ENV) $(VENV_DIR)/bin/python -m pytest api/tests -v

test-web:
	cd web && npm run test

lint: lint-web

lint-web:
	cd web && npm run lint

## --- Local run -----------------------------------------------------------

run-api:
	LUMINARY_PHOTOS_URI=$(LUMINARY_PHOTOS_URI) $(LUMINARY_DATABASE_ENV) \
		LUMINARY_OPENAI_BASE_URL=$(LUMINARY_OPENAI_BASE_URL) LUMINARY_OPENAI_MODEL=$(LUMINARY_OPENAI_MODEL) \
		LUMINARY_FLORENCE_MODEL=$(LUMINARY_FLORENCE_MODEL) \
		PYTHONPATH=api/src $(VENV_DIR)/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

run-web:
	cd web && npm run dev

## --- Container images -----------------------------------------------------

build: build-api build-web

build-api:
	$(CONTAINER_ENGINE) build -t $(API_IMAGE):$(TAG) -f api/Containerfile api

build-web:
	$(CONTAINER_ENGINE) build -t $(WEB_IMAGE):$(TAG) -f web/Containerfile web

images: build

push: push-api push-web

push-api:
	$(CONTAINER_ENGINE) push $(API_IMAGE):$(TAG)

push-web:
	$(CONTAINER_ENGINE) push $(WEB_IMAGE):$(TAG)

## --- Database --------------------------------------------------------------
#
# Provisions/tears down the two local Postgres databases (luminary-dev,
# luminary-unittest) and the shared `luminary` role on the server
# LUMINARY_DATABASE_HOST points at (see db/) -- a separate, already-existing
# Postgres server, not something this repo's Helm chart/ArgoCD app stands
# up. Prod's own "luminary" database (values.yaml's api.env.database.name)
# is provisioned the same way but separately, against prod's own Postgres
# host -- not by these targets. LUMINARY_DATABASE_ADMIN_PASSWORD must be
# supplied for all three targets below.

define CREATE_DATABASE
	PGPASSWORD=$(LUMINARY_DATABASE_ADMIN_PASSWORD) psql \
		-h $(LUMINARY_DATABASE_HOST) -p $(LUMINARY_DATABASE_PORT) -U $(LUMINARY_DATABASE_ADMIN_USER) -d postgres \
		-v db_name=$(1) -v db_user=$(LUMINARY_DATABASE_USER) -v db_password=$(LUMINARY_DATABASE_PASSWORD) \
		-f db/create_db_and_user.sql
	PGPASSWORD=$(LUMINARY_DATABASE_PASSWORD) psql \
		-h $(LUMINARY_DATABASE_HOST) -p $(LUMINARY_DATABASE_PORT) -U $(LUMINARY_DATABASE_USER) -d $(1) \
		-f db/create_tables.sql
endef

define DROP_DATABASE
	PGPASSWORD=$(LUMINARY_DATABASE_ADMIN_PASSWORD) psql \
		-h $(LUMINARY_DATABASE_HOST) -p $(LUMINARY_DATABASE_PORT) -U $(LUMINARY_DATABASE_ADMIN_USER) -d postgres \
		-v db_name=$(1) \
		-f db/drop_database.sql
endef

db:
	$(call CREATE_DATABASE,$(LUMINARY_DATABASE_NAME))
	$(call CREATE_DATABASE,$(LUMINARY_TEST_DATABASE_NAME))

drop-db:
	$(call DROP_DATABASE,$(LUMINARY_DATABASE_NAME))
	$(call DROP_DATABASE,$(LUMINARY_TEST_DATABASE_NAME))
	PGPASSWORD=$(LUMINARY_DATABASE_ADMIN_PASSWORD) psql \
		-h $(LUMINARY_DATABASE_HOST) -p $(LUMINARY_DATABASE_PORT) -U $(LUMINARY_DATABASE_ADMIN_USER) -d postgres \
		-v db_user=$(LUMINARY_DATABASE_USER) \
		-f db/drop_role.sql

recreate-db: drop-db db

## --- Helm -----------------------------------------------------------------

helm-lint:
	helm lint deploy/helm/luminary

helm-template:
	helm template luminary deploy/helm/luminary

## --- Housekeeping ----------------------------------------------------------

clean:
	rm -rf $(TARGET_DIR) web/node_modules web/dist
	find . -name "__pycache__" -type d -exec rm -rf {} +
