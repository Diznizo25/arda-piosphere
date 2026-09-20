# syntax=docker/dockerfile:1
#
# Arda Link — WhatsApp advisory microservice (FastAPI).
#
# What this image IS: a stateless HTTP service. It reads precomputed COGs from R2,
# Postgres/PostGIS for water points and conversation state, and the cached
# environmental series; it talks to WhatsApp and (optionally) Azure for text/TTS.
# It NEVER calls Earth Engine and never renders satellite data.
#
# What it is NOT: the batch pipeline. GEE exports, landmark/water-source imports and
# migrations run from the export machine / GitHub Actions with requirements.txt —
# that is why this image installs requirements-web.txt and does not copy scripts/.
#
# Build:  docker build -t arda-link:latest .
# Run:    docker run --rm -p 8000:8000 --env-file .env arda-link:latest
# Verify: curl http://localhost:8000/health

# --- stage 1: dependencies ---------------------------------------------------
# Wheels are built/baked here once; the runtime stage then carries only what runs.
FROM python:3.11-slim AS deps

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# A compiler is needed for any source build (and to be honest about it rather than
# failing on a wheel that only exists for a different platform).
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements-web.txt .
RUN pip install --upgrade pip && pip install -r requirements-web.txt

# --- stage 2: runtime --------------------------------------------------------
FROM python:3.11-slim AS runtime

# libexpat1 + libgomp1: required by the rasterio/GDAL and numpy wheels.
# curl: used by HEALTHCHECK only.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=deps /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    WEB_CONCURRENCY=2

WORKDIR /app

# Only what the service serves with: app/ (code) + config/ (YAML/GeoJSON the code
# loads at runtime: species rings, advisory thresholds, landmarks, knowledge base).
COPY app ./app
COPY config ./config

# Run as a non-root user: this process handles other people's phone numbers and
# locations, so it should not be able to write to its own code.
RUN useradd --create-home --uid 10001 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Liveness: the app answers /health without touching DB/R2/WhatsApp. Render and
# Kubernetes both use this; a slow cold start is why start-period is generous.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# Render injects $PORT; locally it defaults to 8000.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY} --proxy-headers --forwarded-allow-ips='*'"]
