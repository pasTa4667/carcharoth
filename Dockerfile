FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies (cached unless lockfile changes)
COPY pyproject.toml uv.lock README.md ./

# Sync
RUN uv sync --frozen --no-dev --no-install-project

# Application code
COPY src/ ./src/
COPY config/ ./config/
COPY alembic/ ./alembic/
COPY alembic.ini ./

RUN uv sync --frozen --no-dev

RUN mkdir -p logs

CMD ["uv", "run", "carcharoth"]
