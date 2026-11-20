.PHONY: install build run dev test
install:
	uv venv --python 3.12 .venv
	uv pip sync --python .venv/bin/python backend/requirements-dev.lock
	cd frontend && npm ci
build:
	cd frontend && npm run build
run:
	.venv/bin/python -m backend.app
dev:
	cd frontend && npm run dev
test:
	.venv/bin/python -m pytest -q
	cd frontend && npm test
