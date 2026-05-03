# ForgeAI — Phase 1

Task state machine with PostgreSQL and mock agents (no HTTP, no LLM).

## Prerequisites

- Python 3.11
- PostgreSQL 15+ (Docker Compose is provided)

On Windows, **Docker Desktop must be running** before `docker compose` works.

## Quick start

```bash
copy .env.example .env
docker compose up -d
python -m alembic upgrade head
python main.py
pytest tests/ -v
```

If `python main.py` reports a connection refused error, Postgres is not listening on `DATABASE_URL` (default `localhost:5432`). Start Docker Desktop, run `docker compose up -d`, then run Alembic again.
