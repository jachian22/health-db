FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --upgrade pip && pip install .

EXPOSE 8000

CMD ["sh", "-c", "echo starting health-db-mcp on 0.0.0.0:${PORT:-8000} && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
