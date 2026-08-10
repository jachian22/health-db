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

RUN pip install --upgrade pip && pip install .

EXPOSE 8000

# Inline shell CMD avoids shebang/script issues and always expands PORT.
# Prints diagnostics so Railway deploy logs show whether the process started.
CMD ["sh", "-c", "echo starting health-db on 0.0.0.0:${PORT:-8000} && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
