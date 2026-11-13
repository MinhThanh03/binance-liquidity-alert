#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Missing ${ENV_FILE}. Run install_and_run.bash first." >&2
    exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Missing virtual environment. Run install_and_run.bash first." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

: "${PUSHOVER_APP_TOKEN:?PUSHOVER_APP_TOKEN is missing from .env}"
: "${PUSHOVER_USER_KEY:?PUSHOVER_USER_KEY is missing from .env}"

"${PYTHON_BIN}" - <<'PY'
import os
import sys
from datetime import datetime, timezone

import requests

response = requests.post(
    "https://api.pushover.net/1/messages.json",
    data={
        "token": os.environ["PUSHOVER_APP_TOKEN"],
        "user": os.environ["PUSHOVER_USER_KEY"],
        "title": "Binance Liquidity Alert Test",
        "message": (
            "Test notification successful.\n"
            f"UTC: {datetime.now(timezone.utc).isoformat()}"
        ),
    },
    timeout=20,
)

if not response.ok:
    print(f"Pushover rejected the test notification: {response.text}", file=sys.stderr)
    raise SystemExit(1)

print("Pushover test notification sent successfully.")
PY
