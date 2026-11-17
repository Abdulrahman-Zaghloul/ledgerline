.PHONY: help install up down db migrate run relay test cov lint format typecheck check

help:  ## Show available commands
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-10s %s\n", $$1, $$2}'

install:  ## Install the app and dev tools into the current environment
	pip install -e ".[dev]"

up:  ## Run the full stack (db, migrations, api, relay) in Docker
	docker compose up --build

down:  ## Stop the stack and delete its data
	docker compose down -v

db:  ## Start only PostgreSQL in Docker
	docker compose up -d db

migrate:  ## Apply database migrations
	alembic upgrade head

run:  ## Run the API locally with auto-reload
	uvicorn --factory ledger.main:create_app --reload

relay:  ## Run the outbox relay worker locally
	ledgerline relay

test:  ## Run the test suite
	pytest

cov:  ## Run tests with a coverage report (fails under 90%)
	pytest --cov --cov-report=term-missing --cov-fail-under=90

lint:  ## Check style and common bugs
	ruff check . && ruff format --check .

format:  ## Auto-fix style
	ruff format . && ruff check --fix .

typecheck:  ## Static type checking
	mypy

check: lint typecheck cov  ## Everything CI runs
