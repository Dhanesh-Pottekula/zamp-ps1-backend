# The API as deployed on Railway: Python 3.14, locked dependencies installed with uv, no dev tools.
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# The database and uploaded files live on a mounted volume (/data), so they survive redeploys.
ENV PATH="/app/.venv/bin:$PATH" \
    STORAGE_DB_PATH=/data/ps1.sqlite3 \
    STORAGE_UPLOAD_DIR=/data/uploads

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
