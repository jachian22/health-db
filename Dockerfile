FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts/start.sh ./scripts/start.sh

RUN pip install --upgrade pip && pip install . \
    && chmod +x /app/scripts/start.sh

EXPOSE 8000

# Railway injects PORT. Entrypoint defaults to 8000 if unset.
# Do not run Alembic here — Postgres is not required for a healthy boot.
CMD ["/app/scripts/start.sh"]
