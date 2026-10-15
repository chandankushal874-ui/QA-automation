# Cross-Browser Testing Platform — single shared image (api / worker / fixture)
# Multi-browser: Chromium + Firefox + WebKit (required for full matrix)
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# System libs for Playwright browsers (kept lean: no recommends)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libdbus-1-3 \
    libx11-6 \
    libxcb1 \
    libx11-xcb1 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# App deps first (layer cache). build-essential only for compile, then removed.
# psycopg[binary] = driver v3 — SQLAlchemy 2.x maps `postgresql://` to
# psycopg (v3), NOT psycopg2. Without it the worker silently degrades to
# SQLite and crawls a different database than the API (jobs never visible).
COPY pyproject.toml /app/
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && pip install --upgrade pip \
    && pip install . \
    && pip install "psycopg[binary]" psycopg2-binary gunicorn \
    && playwright install --with-deps chromium firefox webkit \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

COPY src /app/src/
COPY fixture_app /app/fixture_app/

EXPOSE 8000
CMD ["gunicorn", "src.api.app:app", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "-w", "2", "--timeout", "120"]
