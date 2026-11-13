#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${SCRIPT_DIR}/.venv"
ENV_FILE="${SCRIPT_DIR}/.env"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this script as root." >&2
    exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "Python is not installed. Installing Debian packages..."
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        ca-certificates \
        python3 \
        python3-pip \
        python3-venv
fi

if ! "${PYTHON_BIN}" -m venv --help >/dev/null 2>&1; then
    echo "Python venv support is missing. Installing it..."
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv
fi

if [[ ! -f "${ENV_FILE}" ]]; then
    cp "${SCRIPT_DIR}/.env.example" "${ENV_FILE}"
    echo "Created ${ENV_FILE}."
    echo "Edit PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY, then run this script again."
    exit 1
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "Creating local virtual environment..."
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

if ! "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "The virtual environment has no pip. Installing Debian pip/venv support..."
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        python3-pip \
        python3-venv
    echo "Recreating the local virtual environment..."
    rm -rf "${VENV_DIR}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

if ! "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "Unable to initialize pip in ${VENV_DIR}." >&2
    exit 1
fi

echo "Installing Python requirements..."
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r "${SCRIPT_DIR}/requirements.txt"

cd "${SCRIPT_DIR}"
echo "Starting Binance liquidity alert service..."
exec "${VENV_DIR}/bin/python" "${SCRIPT_DIR}/liquidity_alert.py"
