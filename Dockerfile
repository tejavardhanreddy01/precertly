# API image. The project stays an editable install under /app: policy YAML, eval bundles and
# migrations are found relative to src/ (loader.py resolves data/policies as parents[3] of the
# package file), so a wheel in site-packages would not find them.
#   docker build -t precertly-api .
#   docker run --rm -p 8000:8000 precertly-api

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first, so source edits do not invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev

FROM python:3.12-slim
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY --from=build /app/.venv ./.venv
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src ./src
COPY data/policies ./data/policies
COPY migrations ./migrations
COPY evals/bundles ./evals/bundles
COPY scripts/smoke_llm.py ./scripts/smoke_llm.py

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["uvicorn", "precertly.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
