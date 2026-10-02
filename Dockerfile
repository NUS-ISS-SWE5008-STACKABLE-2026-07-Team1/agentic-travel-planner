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

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so editing application code does not reinstall them.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

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
