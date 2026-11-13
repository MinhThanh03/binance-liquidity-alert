#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="${SCRIPT_DIR}/install_and_run.bash"
PID_FILE="${SCRIPT_DIR}/liquidity-alert.pid"
LOG_FILE="${SCRIPT_DIR}/liquidity-alert.log"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this script as root." >&2
    exit 1
fi

read_pid() {
    [[ -f "${PID_FILE}" ]] || return 1
    local pid
    pid="$(<"${PID_FILE}")"
    [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
    printf '%s\n' "${pid}"
}

is_running() {
    local pid="${1}"
    kill -0 "${pid}" 2>/dev/null
}

start_service() {
    local pid
    if pid="$(read_pid)" && is_running "${pid}"; then
        echo "Already running with PID ${pid}."
        return 0
    fi

    rm -f "${PID_FILE}"
    nohup "${RUNNER}" >>"${LOG_FILE}" 2>&1 &
    pid=$!
    printf '%s\n' "${pid}" >"${PID_FILE}"
    echo "Started with PID ${pid}."
    echo "Log: ${LOG_FILE}"
}

stop_service() {
    local pid
    if ! pid="$(read_pid)" || ! is_running "${pid}"; then
        rm -f "${PID_FILE}"
        echo "Not running."
        return 0
    fi

    kill "${pid}"
    for _ in {1..20}; do
        if ! is_running "${pid}"; then
            rm -f "${PID_FILE}"
            echo "Stopped."
            return 0
        fi
        sleep 1
    done

    echo "Process ${pid} did not stop within 20 seconds." >&2
    return 1
}

status_service() {
    local pid
    if pid="$(read_pid)" && is_running "${pid}"; then
        echo "Running with PID ${pid}."
    else
        echo "Stopped."
        return 1
    fi
}

case "${1:-}" in
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    restart)
        stop_service || true
        start_service
        ;;
    status)
        status_service
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}" >&2
        exit 2
        ;;
esac
