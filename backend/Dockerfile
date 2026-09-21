# Multi-stage production image.
#
# Stage 1 (builder): install dependencies into a virtualenv so the final
# layer can copy a clean tree without compilers, headers, or pip caches.
# Stage 2 (runtime): minimal slim image, non-root user, healthcheck wired
# to the /health endpoint that jvspatial's Server exposes by default.
#
# The image runs uvicorn directly. Workers default to 1 because Fly.io
# typically scales horizontally via VMs rather than vertically inside the
# image; override with `WEB_CONCURRENCY` if needed.

FROM python:3.11-slim AS builder

WORKDIR /build
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed_cache

# Build deps for wheels we may compile on slim (bcrypt, cryptography).
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

# Build context is the repo root (see deploy.yml / docker-compose.local.yml).
COPY backend/pyproject.toml backend/README.md ./
COPY backend/app ./app

# Install into a private virtualenv that we'll copy into the final stage.
# All deps (incl. jvagent 0.1.8rc11 + jvspatial 0.0.18) resolve via
# pyproject [tool.uv.sources] (git tips until PyPI/TestPyPI catch up).
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip uv \
 && /opt/venv/bin/uv pip install --python /opt/venv/bin/python .

# Pre-bake the ONNX weights for sentence-transformers/all-MiniLM-L6-v2 so
# the runtime image never fetches from Hugging Face on cold start. The
# instantiation pulls the model + tokenizer artifacts into
# FASTEMBED_CACHE_PATH; the runtime stage copies that directory into the
# same path under the same env var.
RUN mkdir -p /opt/fastembed_cache \
 && /opt/venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2')"

# ---- runtime ---------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HOST=0.0.0.0 \
    PORT=4000 \
    WEB_CONCURRENCY=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed_cache

# Non-root runtime user.
RUN useradd --create-home --uid 10001 --shell /bin/false integral

# Runtime system dependencies:
#   curl         — HEALTHCHECK probe (~1 MB).
#   libmagic1    — MIME sniffing via python-magic (services/attachment_content_sniffer.py).
#   ffmpeg       — provides ffprobe for audio/video metadata (services/attachment_metadata/extractors.py).
#   libreoffice-core, libreoffice-writer, libreoffice-calc, libreoffice-impress
#                — headless office-format preview (services/attachment_preview.py).
# LibreOffice is the largest cost (~350 MB). If image size becomes a problem,
# split office preview into a separate worker image and call it via queue.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      curl \
      libmagic1 \
      ffmpeg \
      libreoffice-core \
      libreoffice-writer \
      libreoffice-calc \
      libreoffice-impress \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/fastembed_cache /opt/fastembed_cache
COPY --from=builder /build/app /app/app
COPY --from=builder /build/pyproject.toml /app/pyproject.toml
# Embedded jvagent bootstrap reads app_root = parents[2]/agent from
# /app/app/main.py → /agent. Baked at build time; local compose may bind-mount
# ./agent over this path for live agent.yaml iteration.
COPY agent /agent

# Writable runtime dirs for the non-root user (named volumes mount as root on
# first create unless the host path is pre-chowned). /tmp/integral_files is
# pre-created + chowned here so docker-compose.local.yml can mount a NAMED
# volume over it and have attachments (payslips, exported registers, NIS/PAYE
# filings) survive an `up -d --build api` — Docker seeds a volume's first
# mount from whatever's already at that path in the image, ownership
# included, so this chown carries through. Without it, every rebuild wiped
# /tmp (container-local, not a volume) while the Attachment rows describing
# those files stayed in Postgres — the file picker still listed them, but
# opening one 404'd with "This file is no longer available."
RUN mkdir -p /data /tmp/integral_files \
 && chown -R integral:integral /opt/fastembed_cache /agent /data /tmp/integral_files

USER integral

EXPOSE 4000

# Liveness, deliberately — NOT /api/health/ready.
#
# A Docker/Swarm HEALTHCHECK conflates liveness and readiness: an unhealthy
# task gets restarted or replaced. /api/health/ready pings the database and
# returns 503 when it is unreachable, so wiring it here would turn a database
# outage into an API restart loop — restarting the API cannot fix the database,
# and the churn removes capacity that would otherwise recover on its own.
#
# /api/health/ready is the right probe for the LOAD BALANCER, which should
# route away from an instance that cannot serve without killing it. To wire
# that up in Swarm, add to the api service's Traefik labels:
#
#   traefik.http.services.<name>.loadbalancer.healthcheck.path=/api/health/ready
#   traefik.http.services.<name>.loadbalancer.healthcheck.interval=10s
#
# Left unset here because it changes production routing and could not be
# verified from a dev machine; apply and confirm against a live stack.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/health" >/dev/null || exit 1

# --forwarded-allow-ips: which immediate peers may rewrite scope["client"] /
# scope["scheme"] from X-Forwarded-For / X-Forwarded-Proto. It used to be '*',
# which made uvicorn trust the header from EVERY peer and set request.client
# to the LEFTMOST (caller-typed) X-Forwarded-For entry — so the login / OTP
# rate limiter (app/middleware/rate_limit.py) bucketed on an attacker-chosen
# IP. The default below is the loopback + RFC1918 / ULA ranges the reverse
# proxy (Traefik on the Swarm overlay, or the nginx `web` container) actually
# connects from; for a trusted peer uvicorn walks X-Forwarded-For right-to-
# left past trusted hops, so request.client becomes the real client, and an
# untrusted peer keeps its socket address. --proxy-headers stays on so
# X-Forwarded-Proto still yields https for absolute URLs (OAuth consent).
# Override with FORWARDED_ALLOW_IPS (CSV of IPs / CIDRs) when the proxy sits
# on a public address. Keep RATE_LIMIT_TRUSTED_PROXIES in agreement.
ENV FORWARDED_ALLOW_IPS="127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"

CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST} --port ${PORT} --workers ${WEB_CONCURRENCY} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS}\""]

# ---- core (F0 closeout) ----------------------------------------------------
# Slim Core release artifact: only core_package seeds on disk, domain Apps
# excluded. Build with: docker build --target core -f backend/Dockerfile .
FROM runtime AS core

USER root
# Keep agent-scratch; drop every other package directory.
RUN if [ -d /app/app/packages ]; then \
      find /app/app/packages -mindepth 1 -maxdepth 1 -type d \
        ! -name 'agent-scratch' \
        ! -name '__pycache__' \
        -exec rm -rf {} + ; \
    fi
ENV INTEGRAL_CORE_ONLY=1
USER integral

# Default target for bare `docker build` / compose (product image with Apps).
# Core release artifact: `docker build --target core -f backend/Dockerfile .`
FROM runtime
# Default OSS image: docker build --target core -f backend/Dockerfile .
