# One image, two roles (ADR-0013). The command decides which:
#
#   web     gunicorn "flaskapp:create_app()"        (the default CMD below)
#   agents  python -m scripts.a2a_server --host 0.0.0.0 --port 8000
#           (-m, so /app is on sys.path; the script path form cannot import flaskapp)
#
# The web role reaches the agents role over official A2A when the environment
# sets A2A_INTERNAL_ENABLED=true and A2A_BASE_URL to the agents address. See
# deploy/k8s/ and deploy/docker-compose.yml.

# Matches PYTHON_VERSION in render.yaml, so both deploy targets run the same
# interpreter. Raise them together.
FROM python:3.11.9-slim-bookworm

# VARIANT A, under measurement. See docs/security/image-dast-experiment.md.
#
# The image DAST experiment put Trivy over these layers for the first time and
# found 11 CRITICAL and 120 HIGH, every critical of them a Debian package
# (openssl, libgnutls30, perl-base, libexpat1, zlib1g, libsqlite3-0) with a
# published fix. pip-audit never saw any of it: it reads requirements.lock,
# which says nothing about the operating system underneath.
#
# The tag is pinned, which is what froze these packages at the date that tag
# was built. This re-fetches the security updates Debian has published since.
#
# THE HONEST COST: this step is not reproducible. Two builds of the same commit
# on different days can produce different packages, which cuts against the
# pinning discipline everywhere else here (hash-locked requirements, actions
# pinned to commit SHAs). The alternative under measurement is raising the base
# tag instead, which stays reproducible but must move render.yaml's
# PYTHON_VERSION in lockstep.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# CVE-2024-6345 (HIGH) is setuptools 65.5.1, which ships inside the base image
# rather than coming from requirements.lock, so neither pip-audit nor the lock
# file can reach it.
RUN python -m pip install --upgrade "setuptools>=78.1.1"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so editing application code does not reinstall them.
# The hash-locked file, not requirements.txt: the ranges in requirements.txt
# would let the image pick up versions CI never tested and pip-audit never
# scanned. --require-hashes refuses any file whose hash differs from the lock;
# --only-binary refuses source distributions, whose setup scripts run code at
# install time. Every locked package ships a wheel for this platform.
COPY requirements.lock .
RUN pip install --require-hashes --only-binary :all: -r requirements.lock

# Only what runs. Copying the whole context would also take anything a
# .dockerignore line missed; naming the paths makes the image's contents a
# reviewed list. scripts/ is needed by the agents role (scripts.a2a_server)
# and by the local compose stub (scripts.ci_stub_provider).
COPY app.py asgi.py gunicorn.conf.py ./
COPY flaskapp/ flaskapp/
COPY scripts/ scripts/

# A fixed non-root uid, so Kubernetes can enforce runAsNonRoot. instance/ is
# where TRACE_DIR and the SQLite fallback live, and it is excluded from the
# build context, so it is created here and owned by that user.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p instance/traces \
    && chown -R app:app instance
USER 10001

EXPOSE 8000

# Same shape as render.yaml's startCommand. One worker per pod: job state is in
# the database now, so more pods are how the web role scales (deploy/k8s/hpa.yaml),
# and one process keeps the plans-in-flight count it publishes simple.
# gunicorn.conf.py adds the graceful drain and that metrics endpoint.
CMD ["gunicorn", "flaskapp:create_app()", \
     "--config", "gunicorn.conf.py", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "1", "--threads", "8", "--timeout", "120"]
