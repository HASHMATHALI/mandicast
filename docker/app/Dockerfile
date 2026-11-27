# syntax=docker/dockerfile:1
# MandiCast Python services: migrations, API, worker (scheduler), Telegram bot, MLflow UI (docs/15).
# One image; the Compose service chooses the command. Runs as an unprivileged user; data, models, reports, logs and
# experiments are mounted volumes, never baked into the image (they are git-ignored and can hold secrets in logs).

# python:3.14-slim (3.14.10), pinned by digest
FROM python:3.14-slim@sha256:22eafb370920ad8b429e0aec3fff433e0669968519f5d60469d83729c138b28b AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONPATH=/app/src \
    HOME=/tmp \
    NUMBA_CACHE_DIR=/tmp/numba \
    MPLCONFIGDIR=/tmp/matplotlib

# LightGBM needs the OpenMP runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-linux.lock.txt ./
# Exact versions (a complete freeze) with SHA-256 hashes: no resolver run, no unpinned transitive dependency, and a
# file that differs from the locked one is refused.
RUN pip install --no-deps --require-hashes -r requirements-linux.lock.txt

COPY alembic.ini pyproject.toml ./
COPY migrations ./migrations
COPY config ./config
COPY src ./src

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin mandicast \
    && mkdir -p data models reports logs experiments \
    && chown mandicast data models reports logs experiments
USER mandicast

ENTRYPOINT ["python", "-m", "mandicast"]
CMD ["api", "--host", "0.0.0.0", "--port", "8000"]

# Test image: the repository as a fresh clone would have it (build it from such a copy; see docs/15 §5).
FROM runtime AS test
USER root
COPY . /app
RUN chown -R mandicast /app
USER mandicast
ENTRYPOINT []
CMD ["sh", "-c", "python -m ruff check src tests scripts migrations && python -m pytest -p no:cacheprovider"]
