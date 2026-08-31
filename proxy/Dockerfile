FROM python:3.13-slim AS base

# uv gives us fast, deterministic installs without a separate lockfile tool.
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY demo-data.json ./demo-data.json
COPY scenarios ./scenarios

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
