FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    ORCHESTRA_RUNTIME_DIR=/app/runtime \
    ORCHESTRA_WEB__HOST=0.0.0.0 \
    ORCHESTRA_WEB__PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-dev.txt pyproject.toml README.md ./
COPY orchestra ./orchestra
COPY exchanges.yaml ./exchanges.yaml

RUN pip install --no-cache-dir -r requirements.txt -e . \
    && useradd --create-home --shell /bin/bash orchestra \
    && mkdir -p /app/runtime \
    && chown -R orchestra:orchestra /app

USER orchestra

EXPOSE 8000

CMD ["python", "-m", "orchestra", "serve"]
