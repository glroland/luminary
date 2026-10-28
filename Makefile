CONTAINER_ENGINE ?= podman
IMAGE_REGISTRY   ?= quay.io/yourorg
API_IMAGE        ?= $(IMAGE_REGISTRY)/luminary-api
WEB_IMAGE        ?= $(IMAGE_REGISTRY)/luminary-web
TAG              ?= latest

# All local, gitignored run/build artifacts (venv, local SQLite DB + photo
# storage, pytest cache) live under here -- `make clean` just removes it.
TARGET_DIR  ?= target
VENV_DIR    := $(TARGET_DIR)/venv
DATA_DIR    := $(TARGET_DIR)/data

.PHONY: help install install-api install-web \
        test test-api test-web \
        lint lint-web \
        run-api run-web \
        build build-api build-web \
        push push-api push-web \
        images \
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
	$(VENV_DIR)/bin/python -m pytest api/tests -v

test-web:
	cd web && npm run test

lint: lint-web

lint-web:
	cd web && npm run lint

## --- Local run -----------------------------------------------------------

run-api:
	LUMINARY_PHOTOS_URI=$(DATA_DIR)/photos LUMINARY_DATABASE_PATH=$(DATA_DIR)/photos.db \
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

## --- Helm -----------------------------------------------------------------

helm-lint:
	helm lint deploy/helm/luminary

helm-template:
	helm template luminary deploy/helm/luminary

## --- Housekeeping ----------------------------------------------------------

clean:
	rm -rf $(TARGET_DIR) web/node_modules web/dist
	find . -name "__pycache__" -type d -exec rm -rf {} +
