.PHONY: up down logs test test-smoke lint format migrate health deps typecheck test-cov

up:
	docker compose -f docker/docker-compose.yml up --build -d

down:
	docker compose -f docker/docker-compose.yml down

logs:
	docker compose -f docker/docker-compose.yml logs -f

deps:
	pip install -r requirements-dev.txt

test:
	pytest

test-smoke:
	pytest -m smoke

test-cov:
	pytest --cov=providers --cov=context --cov=routing --cov-report=term-missing

lint:
	ruff check .

typecheck:
	mypy .

format:
	black . && ruff --fix .

migrate:
	alembic upgrade head

health:
	curl -s http://localhost:8000/health
