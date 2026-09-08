#!/usr/bin/env bash
# Run this application as a System Under Test for the official A2A TCK.
#
# Uses scripts/ci_stub_provider.py as the model, so a conformance run costs no
# credentials and no billed calls - the TCK exercises protocol behaviour, not
# planning quality. Start the stub first:
#
#   python scripts/ci_stub_provider.py &
#   bash scripts/run_tck_sut.sh &
#   ./run_tck.py --sut-host http://127.0.0.1:8080 --transport jsonrpc
#
# A2A_ROOT_AGENT decides which agent answers at the origin's well-known path.
# The TCK looks only there, so without it a run cannot start.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${TCK_SUT_PORT:-8080}"
export A2A_ROOT_AGENT="${A2A_ROOT_AGENT:-flight_agent}"
export A2A_BASE_URL="http://127.0.0.1:${PORT}"
export LLM_PROVIDER=openai_compatible
export LLM_API_KEY=stub
export LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:8081/v1}"
export LLM_MODEL=stub-model
export FLIGHT_INVENTORY_SOURCE=seed
export SECRET_KEY="${SECRET_KEY:-tck-run-only-not-a-secret}"

# uvicorn, not gunicorn: asgi:application is ASGI and gunicorn's default worker
# is WSGI. This is also why render.yaml cannot serve the A2A routes today.
exec python -m uvicorn asgi:application --host 127.0.0.1 --port "${PORT}"
