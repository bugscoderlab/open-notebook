.PHONY: run frontend check ruff database database-local database-local-fg database-local-stop dev-up dev-down dev-status test test-cov test-integration test-testpack test-durations lint api start-all stop-all status clean-cache worker worker-start worker-stop worker-restart gateway gateway-stop
.PHONY: docker-buildx-prepare docker-buildx-clean docker-buildx-reset
.PHONY: docker-push docker-push-latest docker-release docker-build-local tag export-docs
.PHONY: release-test release-stack release-stack-down

# Get version from pyproject.toml
VERSION := $(shell grep -m1 version pyproject.toml | cut -d'"' -f2)

# Image names for both registries
DOCKERHUB_IMAGE := lfnovo/open_notebook
GHCR_IMAGE := ghcr.io/lfnovo/open-notebook

# Build platforms
PLATFORMS := linux/amd64,linux/arm64

database:
	docker compose up -d surrealdb

# Native (no-Docker) SurrealDB for localhost development.
# Uses a locally installed SurrealDB binary with a repo-local data dir.
# IMPORTANT: must be SurrealDB v2.x (the migrations use v2 syntax; v3 rejects
# them). Docker mode uses the same major version. Get a v2 binary from
# https://github.com/surrealdb/surrealdb/releases (e.g. surreal-v2.x.y.darwin-arm64.tgz)
# and either put it on PATH as `surreal` or point SURREAL_BIN at it.
SURREAL_BIN ?= surreal
LOCAL_SURREAL_USER ?= root
LOCAL_SURREAL_PASS ?= root
LOCAL_SURREAL_DIR := $(CURDIR)/.surrealdb-local
LOCAL_SURREAL_PORT ?= 8000

database-local:
	@echo "Starting SurrealDB ($(SURREAL_BIN)) on port $(LOCAL_SURREAL_PORT) (data: $(LOCAL_SURREAL_DIR))"
	@mkdir -p $(LOCAL_SURREAL_DIR)
	@$(SURREAL_BIN) start --user $(LOCAL_SURREAL_USER) --pass $(LOCAL_SURREAL_PASS) --bind 0.0.0.0:$(LOCAL_SURREAL_PORT) rocksdb://$(LOCAL_SURREAL_DIR)/mydatabase.db & \
		echo $$! > $(LOCAL_SURREAL_DIR)/surreal.pid
	@echo "SurrealDB starting (pid in $(LOCAL_SURREAL_DIR)/surreal.pid). Use 'make database-local-stop' to stop."

database-local-stop:
	@if [ -f $(LOCAL_SURREAL_DIR)/surreal.pid ]; then \
		kill $$(cat $(LOCAL_SURREAL_DIR)/surreal.pid) 2>/dev/null || true; \
		rm -f $(LOCAL_SURREAL_DIR)/surreal.pid; \
		echo "SurrealDB stopped."; \
	else \
		pkill -f "surreal start" 2>/dev/null && echo "SurrealDB stopped." || echo "No local SurrealDB running."; \
	fi

# Foreground SurrealDB for Herdr/dev-herdr.sh (status visible in the pane).
database-local-fg:
	@mkdir -p $(LOCAL_SURREAL_DIR)
	$(SURREAL_BIN) start --user $(LOCAL_SURREAL_USER) --pass $(LOCAL_SURREAL_PASS) --bind 0.0.0.0:$(LOCAL_SURREAL_PORT) rocksdb://$(LOCAL_SURREAL_DIR)/mydatabase.db

# Dev stack in Herdr tabs (one server per tab). Requires running inside Herdr.
dev-up:
	@bash scripts/dev-herdr.sh up

dev-down:
	@bash scripts/dev-herdr.sh down

dev-status:
	@bash scripts/dev-herdr.sh status

# Fast local tests: unit tier only (default), parallel workers, no coverage.
# Integration/testpack tiers are excluded by default addopts (see pyproject).
test:
	uv run pytest tests/ -n auto

# CI-equivalent: serial coverage run matching .github/workflows/test.yml.
test-cov:
	uv run pytest tests/ -v --cov=open_notebook --cov=api --cov-report=term-missing --cov-report=xml

# Integration tier: suites against live SurrealDB.
# Each xdist worker gets its own SurrealDB namespace (open_notebook_test_gwN)
# — see tests/conftest.py. Needs: SurrealDB (make database-local).
test-integration:
	OPEN_NOTEBOOK_TEST_TIER=integration uv run pytest -m integration -n auto

# Testpack tier: the synthetic acceptance pack (canary sweep, access matrix,
# real-PDF extraction) plus the integration suites. Matches the CI testpack job.
test-testpack:
	OPEN_NOTEBOOK_TEST_TIER=testpack uv run pytest -m "testpack or integration" -n auto

# Regenerate .test_durations for the pytest-split CI shards.
test-durations:
	uv run pytest tests/ -n auto --store-durations --durations-path .test_durations

run:
	@echo "⚠️  Warning: Starting frontend only. For full functionality, use 'make start-all'"
	cd frontend && npm run dev

frontend:
	cd frontend && npm run dev

lint:
	uv run python -m mypy .

ruff:
	ruff check . --fix

# === Docker Build Setup ===
docker-buildx-prepare:
	@docker buildx inspect multi-platform-builder >/dev/null 2>&1 || \
		docker buildx create --use --name multi-platform-builder --driver docker-container
	@docker buildx use multi-platform-builder

docker-buildx-clean:
	@echo "🧹 Cleaning up buildx builders..."
	@docker buildx rm multi-platform-builder 2>/dev/null || true
	@docker ps -a | grep buildx_buildkit | awk '{print $$1}' | xargs -r docker rm -f 2>/dev/null || true
	@echo "✅ Buildx cleanup complete!"

docker-buildx-reset: docker-buildx-clean docker-buildx-prepare
	@echo "✅ Buildx reset complete!"

# === Release Testing (see .github/RELEASE_PROCESS.md) ===

# Automated image gate: fresh install + upgrade against real images.
# Usage: make release-test TAG=1.12.0 OLD_TAG=1.11.0
release-test:
	@test -n "$(TAG)" || (echo "usage: make release-test TAG=<new> [OLD_TAG=<previous>]"; exit 1)
	bash scripts/release-test/release-image-test.sh all \
		"$(DOCKERHUB_IMAGE):$(TAG)" \
		$(if $(OLD_TAG),"$(DOCKERHUB_IMAGE):$(OLD_TAG)")

# Browsable RC stack for manual verification (optionally with a data dump).
# Usage: make release-stack TAG=1.12.0 [DUMP=/tmp/dev-dump.surql]
release-stack:
	@test -n "$(TAG)" || (echo "usage: make release-stack TAG=<tag> [DUMP=<dump.surql>]"; exit 1)
	bash scripts/release-test/rc-stack.sh up "$(TAG)" $(DUMP)

release-stack-down:
	bash scripts/release-test/rc-stack.sh down "$(or $(TAG),unused)"

# === Docker Build Targets ===

# Build production image for local platform only (no push)
docker-build-local:
	@echo "🔨 Building production image locally ($(shell uname -m))..."
	docker build \
		-t $(DOCKERHUB_IMAGE):$(VERSION) \
		-t $(DOCKERHUB_IMAGE):local \
		.
	@echo "✅ Built $(DOCKERHUB_IMAGE):$(VERSION) and $(DOCKERHUB_IMAGE):local"
	@echo "Run with: docker run -p 5055:5055 -p 3000:3000 $(DOCKERHUB_IMAGE):local"

# Build and push version tags ONLY (no latest) for both regular and single images
docker-push: docker-buildx-prepare
	@echo "📤 Building and pushing version $(VERSION) to both registries..."
	@echo "🔨 Building regular image..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		-t $(DOCKERHUB_IMAGE):$(VERSION) \
		-t $(GHCR_IMAGE):$(VERSION) \
		--push \
		.
	@echo "🔨 Building single-container image..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		--target single \
		-t $(DOCKERHUB_IMAGE):$(VERSION)-single \
		-t $(GHCR_IMAGE):$(VERSION)-single \
		--push \
		.
	@echo "✅ Pushed version $(VERSION) to both registries (latest NOT updated)"
	@echo "  📦 Docker Hub:"
	@echo "    - $(DOCKERHUB_IMAGE):$(VERSION)"
	@echo "    - $(DOCKERHUB_IMAGE):$(VERSION)-single"
	@echo "  📦 GHCR:"
	@echo "    - $(GHCR_IMAGE):$(VERSION)"
	@echo "    - $(GHCR_IMAGE):$(VERSION)-single"

# Update v1-latest tags to current version (both regular and single images)
docker-push-latest: docker-buildx-prepare
	@echo "📤 Updating v1-latest tags to version $(VERSION)..."
	@echo "🔨 Building regular image with latest tag..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		-t $(DOCKERHUB_IMAGE):$(VERSION) \
		-t $(DOCKERHUB_IMAGE):v1-latest \
		-t $(GHCR_IMAGE):$(VERSION) \
		-t $(GHCR_IMAGE):v1-latest \
		--push \
		.
	@echo "🔨 Building single-container image with latest tag..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		--target single \
		-t $(DOCKERHUB_IMAGE):$(VERSION)-single \
		-t $(DOCKERHUB_IMAGE):v1-latest-single \
		-t $(GHCR_IMAGE):$(VERSION)-single \
		-t $(GHCR_IMAGE):v1-latest-single \
		--push \
		.
	@echo "✅ Updated v1-latest to version $(VERSION)"
	@echo "  📦 Docker Hub:"
	@echo "    - $(DOCKERHUB_IMAGE):$(VERSION) → v1-latest"
	@echo "    - $(DOCKERHUB_IMAGE):$(VERSION)-single → v1-latest-single"
	@echo "  📦 GHCR:"
	@echo "    - $(GHCR_IMAGE):$(VERSION) → v1-latest"
	@echo "    - $(GHCR_IMAGE):$(VERSION)-single → v1-latest-single"

# Full release: push version AND update latest tags
docker-release: docker-push-latest
	@echo "✅ Full release complete for version $(VERSION)"

tag:
	@version=$$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'); \
	echo "Creating tag v$$version"; \
	git tag "v$$version"; \
	git push origin "v$$version"


dev:
	docker compose -f examples/docker-compose-dev.yml --project-directory . up --build

full:
	docker compose -f examples/docker-compose-full-local.yml --project-directory . up --build


api:
	uv run --env-file .env run_api.py

.PHONY: worker worker-start worker-stop worker-restart

worker: worker-start

worker-start:
	@echo "Starting surreal-commands worker..."
	uv run --env-file .env surreal-commands-worker --import-modules commands --max-tasks "$${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-5}"

worker-stop:
	@echo "Stopping surreal-commands worker..."
	pkill -f "surreal-commands-worker" || true

# Messenger gateway (Telegram/WhatsApp adapters). Idles with no platform
# connections unless the adapter env vars are set; needs the API healthy.
gateway:
	cd gateway && npm run dev

gateway-stop:
	pkill -f "tsx src/index.ts" || true
	pkill -f "node dist/index.js" || true

worker-restart: worker-stop
	@deadline=$$(( $$(date +%s) + 10 )); \
	while pgrep -f "surreal-commands-worker" > /dev/null 2>&1; do \
		if [ "$$(date +%s)" -ge "$$deadline" ]; then \
			echo "Worker still running after 10s; starting anyway."; \
			break; \
		fi; \
		sleep 0.5; \
	done
	@$(MAKE) worker-start

# === Service Management ===
start-all:
	@echo "🚀 Starting Open Notebook (Database + API + Worker + Frontend)..."
	@echo "📊 Starting SurrealDB..."
	@docker compose up -d surrealdb
	@scripts/wait-http.sh http://localhost:8000/health 60 surrealdb
	@echo "🔧 Starting API backend..."
	@uv run run_api.py &
	@scripts/wait-http.sh http://localhost:5055/health 180 api
	@echo "⚙️ Starting background worker..."
	@uv run --env-file .env surreal-commands-worker --import-modules commands --max-tasks "$${OPEN_NOTEBOOK_WORKER_MAX_TASKS:-5}" &
	@deadline=$$(( $$(date +%s) + 30 )); \
	until pgrep -f "surreal-commands-worker" > /dev/null 2>&1; do \
		if [ "$$(date +%s)" -ge "$$deadline" ]; then \
			echo "ERROR: worker did not start within 30s" >&2; \
			exit 1; \
		fi; \
		sleep 1; \
	done
	@echo "💬 Starting messenger gateway..."
	@cd gateway && ([ -d node_modules ] || npm ci) && (npm run dev > /dev/null 2>&1 &)
	@echo "🌐 Starting Next.js frontend..."
	@echo "✅ All services started!"
	@echo "📱 Frontend: http://localhost:3000"
	@echo "🔗 API: http://localhost:5055"
	@echo "📚 API Docs: http://localhost:5055/docs"
	cd frontend && npm run dev

stop-all:
	@echo "🛑 Stopping all Open Notebook services..."
	@pkill -f "next dev" || true
	@pkill -f "tsx src/index.ts" || true
	@pkill -f "surreal-commands-worker" || true
	@pkill -f "run_api.py" || true
	@pkill -f "uvicorn api.main:app" || true
	@docker compose down
	@echo "✅ All services stopped!"

status:
	@echo "📊 Open Notebook Service Status:"
	@echo "Database (SurrealDB):"
	@docker compose ps surrealdb 2>/dev/null || echo "  ❌ Not running"
	@echo "API Backend:"
	@pgrep -f "run_api.py\|uvicorn api.main:app" >/dev/null && echo "  ✅ Running" || echo "  ❌ Not running"
	@echo "Background Worker:"
	@pgrep -f "surreal-commands-worker" >/dev/null && echo "  ✅ Running" || echo "  ❌ Not running"
	@echo "Messenger Gateway:"
	@pgrep -f "tsx src/index.ts" >/dev/null && echo "  ✅ Running (dev)" || (pgrep -f "node dist/index.js" >/dev/null && echo "  ✅ Running" || echo "  ❌ Not running")
	@echo "Next.js Frontend:"
	@pgrep -f "next dev" >/dev/null && echo "  ✅ Running" || echo "  ❌ Not running"

# === Documentation Export ===
export-docs:
	@echo "📚 Exporting documentation..."
	@uv run python scripts/export_docs.py
	@echo "✅ Documentation export complete!"

# === Cleanup ===
clean-cache:
	@echo "🧹 Cleaning cache directories..."
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".mypy_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".ruff_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -type f -delete 2>/dev/null || true
	@find . -name "*.pyo" -type f -delete 2>/dev/null || true
	@find . -name "*.pyd" -type f -delete 2>/dev/null || true
	@echo "✅ Cache directories cleaned!"