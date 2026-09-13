FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:${PATH}" \
    PORT=8080

WORKDIR /app

# Candidate deployments pass the exact approved source SHA at build time.  The
# normal service build leaves it empty; the paused V7 readback requires the
# candidate image to prove its source rather than echoing request metadata.
ARG V7_PAPER_SOURCE_COMMIT=""
ENV V7_PAPER_SOURCE_COMMIT=${V7_PAPER_SOURCE_COMMIT}
LABEL org.opencontainers.image.revision=${V7_PAPER_SOURCE_COMMIT}

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN python -m pip install --upgrade pip uv \
    && uv sync --frozen --no-dev

CMD ["gunicorn", "--bind", ":8080", "--workers", "1", "--threads", "1", "--timeout", "300", "main:app"]
