#!/usr/bin/env python3
"""Binance 1-hour liquidity sweep alerts via Pushover."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


BINANCE_EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
BINANCE_URL = "https://fapi.binance.com/fapi/v1/klines"
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
INTERVAL = "1h"


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None else float(value)


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)


SYMBOLS = tuple(
    symbol.strip().upper()
    for symbol in os.getenv(
        "SYMBOLS", "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT"
    ).split(",")
    if symbol.strip()
)
EMA_LENGTH = env_int("EMA_LENGTH", 50)
PIVOT_LENGTH = env_int("PIVOT_LENGTH", 5)
STORED_LEVELS = env_int("STORED_LEVELS", 20)
ATR_LENGTH = env_int("ATR_LENGTH", 14)
MIN_SWEEP_DISTANCE_ATR = env_float("MIN_SWEEP_DISTANCE_ATR", 0.10)
MIN_WICK_PERCENT = env_float("MIN_WICK_PERCENT", 0.35)
MAX_BODY_PERCENT = env_float("MAX_BODY_PERCENT", 0.65)
MIN_CANDLE_RANGE_ATR = env_float("MIN_CANDLE_RANGE_ATR", 0.20)
CONFIRMATION_WINDOW = env_int("CONFIRMATION_WINDOW", 2)
SIGNAL_COOLDOWN_BARS = env_int("SIGNAL_COOLDOWN_BARS", 10)
STOP_LOSS_ATR_MULTIPLIER = env_float("STOP_LOSS_ATR_MULTIPLIER", 1.5)
TAKE_PROFIT_RR = env_float("TAKE_PROFIT_RR", 3.0)
USE_EMA_FILTER = env_bool("USE_EMA_FILTER", True)
REQUIRE_MIDLINE_BREAK = env_bool("REQUIRE_MIDLINE_BREAK", True)
REQUIRE_BULLISH_BODY = env_bool("REQUIRE_BULLISH_BODY", True)
REQUIRE_BEARISH_BODY = env_bool("REQUIRE_BEARISH_BODY", True)
POLL_SECONDS = env_int("POLL_SECONDS", 60)
KLINE_LIMIT = max(150, env_int("KLINE_LIMIT", 500))
STATE_FILE = Path(os.getenv("STATE_FILE", "./state.json"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float


@dataclass
class Signal:
    direction: int
    candle: Candle
    score: float
    entry: float
    stop: float
    target: float
    reason: str


def ema(values: list[float], length: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < length:
        return result
    multiplier = 2.0 / (length + 1)
    current = sum(values[:length]) / length
    result[length - 1] = current
    for index in range(length, len(values)):
        current = (values[index] - current) * multiplier + current
        result[index] = current
    return result


def atr(candles: list[Candle], length: int) -> list[float | None]:
    true_ranges: list[float] = []
    for index, candle in enumerate(candles):
        previous_close = candles[index - 1].close if index else candle.close
        true_ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
    result: list[float | None] = [None] * len(candles)
    if len(candles) < length:
        return result
    current = sum(true_ranges[:length]) / length
    result[length - 1] = current
    for index in range(length, len(candles)):
        current = ((current * (length - 1)) + true_ranges[index]) / length
        result[index] = current
    return result


def pivot_high(candles: list[Candle], index: int, length: int) -> bool:
    if index < length or index + length >= len(candles):
        return False
    value = candles[index].high
    return all(
        value > candles[other].high
        for other in range(index - length, index + length + 1)
        if other != index
    )


def pivot_low(candles: list[Candle], index: int, length: int) -> bool:
    if index < length or index + length >= len(candles):
        return False
    value = candles[index].low
    return all(
        value < candles[other].low
        for other in range(index - length, index + length + 1)
        if other != index
    )


def quality_score(
    wick_percent: float,
    body_percent: float,
    range_atr: float,
    minimum_wick: float,
    maximum_body: float,
    minimum_range_atr: float,
    ema_ok: bool,
    reclaim_ok: bool,
    midline_ok: bool,
) -> float:
    wick_score = (
        32.0
        if wick_percent >= minimum_wick
        else max(0.0, wick_percent / max(minimum_wick, 1e-12) * 32.0)
    )
    body_score = (
        24.0
        if body_percent <= maximum_body
        else max(0.0, (1.0 - body_percent) / max(maximum_body, 1e-12) * 24.0)
    )
    range_score = (
        18.0
        if range_atr >= minimum_range_atr
        else max(0.0, range_atr / max(minimum_range_atr, 1e-12) * 18.0)
    )
    return min(
        100.0,
        max(
            0.0,
            wick_score
            + body_score
            + range_score
            + (10.0 if ema_ok else 0.0)
            + (10.0 if reclaim_ok else 0.0)
            + (6.0 if midline_ok else 0.0),
        ),
    )


def fetch_futures_symbols(session: requests.Session) -> set[str]:
    response = session.get(BINANCE_EXCHANGE_INFO_URL, timeout=20)
    response.raise_for_status()
    payload = response.json()
    symbols = {
        item["symbol"]
        for item in payload.get("symbols", [])
        if item.get("status") == "TRADING"
        and item.get("contractType") == "PERPETUAL"
        and item.get("quoteAsset") == "USDT"
    }
    if not symbols:
        raise RuntimeError("Binance returned no trading USDT perpetual futures")
    return symbols


def fetch_candles(session: requests.Session, symbol: str) -> list[Candle]:
    response = session.get(
        BINANCE_URL,
        params={"symbol": symbol, "interval": INTERVAL, "limit": KLINE_LIMIT},
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"Binance returned no candles for {symbol}")
    # The final Binance kline is still forming. Signals are checked on closes only.
    rows = rows[:-1]
    return [
        Candle(
            open_time=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
        )
        for row in rows
    ]


def analyze(candles: list[Candle]) -> tuple[str, Signal | None]:
    if len(candles) < max(EMA_LENGTH, ATR_LENGTH) + PIVOT_LENGTH * 2 + 5:
        return "WAITING", None

    closes = [candle.close for candle in candles]
    emas = ema(closes, EMA_LENGTH)
    atrs = atr(candles, ATR_LENGTH)
    high_levels: list[tuple[float, int]] = []
    low_levels: list[tuple[float, int]] = []
    pending_bull: dict[str, Any] | None = None
    pending_bear: dict[str, Any] | None = None
    last_signal_index: int | None = None
    active: Signal | None = None
    for index, candle in enumerate(candles):
        # A pivot is only known after PIVOT_LENGTH bars to its right have closed.
        pivot_index = index - PIVOT_LENGTH
        if pivot_high(candles, pivot_index, PIVOT_LENGTH):
            high_levels.append((candles[pivot_index].high, pivot_index))
            high_levels = high_levels[-STORED_LEVELS:]
        if pivot_low(candles, pivot_index, PIVOT_LENGTH):
            low_levels.append((candles[pivot_index].low, pivot_index))
            low_levels = low_levels[-STORED_LEVELS:]

        current_atr = atrs[index]
        current_ema = emas[index]
        if current_atr is None or current_ema is None or current_atr <= 0:
            continue

        candle_range = candle.high - candle.low
        if candle_range <= 0:
            continue
        body_percent = abs(candle.close - candle.open) / candle_range
        upper_wick_percent = (
            candle.high - max(candle.open, candle.close)
        ) / candle_range
        lower_wick_percent = (
            min(candle.open, candle.close) - candle.low
        ) / candle_range
        range_atr = candle_range / current_atr
        sweep_distance = MIN_SWEEP_DISTANCE_ATR * current_atr

        swept_high = next(
            (
                level
                for level, level_index in reversed(high_levels)
                if level_index < index and candle.high >= level + sweep_distance
            ),
            None,
        )
        swept_low = next(
            (
                level
                for level, level_index in reversed(low_levels)
                if level_index < index and candle.low <= level - sweep_distance
            ),
            None,
        )
        bearish_reclaim = (
            swept_high is not None
            and candle.close < swept_high
            and (
                not REQUIRE_MIDLINE_BREAK
                or candle.close < (candle.high + candle.low) / 2
            )
        )
        bullish_reclaim = (
            swept_low is not None
            and candle.close > swept_low
            and (
                not REQUIRE_MIDLINE_BREAK
                or candle.close > (candle.high + candle.low) / 2
            )
        )
        valid_sell_sweep = (
            bearish_reclaim
            and upper_wick_percent >= MIN_WICK_PERCENT
            and body_percent <= MAX_BODY_PERCENT
            and range_atr >= MIN_CANDLE_RANGE_ATR
        )
        valid_buy_sweep = (
            bullish_reclaim
            and lower_wick_percent >= MIN_WICK_PERCENT
            and body_percent <= MAX_BODY_PERCENT
            and range_atr >= MIN_CANDLE_RANGE_ATR
        )

        if valid_buy_sweep:
            pending_bull = {
                "index": index,
                "mid": (candle.high + candle.low) / 2,
                "score": quality_score(
                    lower_wick_percent,
                    body_percent,
                    range_atr,
                    MIN_WICK_PERCENT,
                    MAX_BODY_PERCENT,
                    MIN_CANDLE_RANGE_ATR,
                    not USE_EMA_FILTER or candle.close > current_ema,
                    bullish_reclaim,
                    not REQUIRE_MIDLINE_BREAK
                    or candle.close > (candle.high + candle.low) / 2,
                ),
            }
        if valid_sell_sweep:
            pending_bear = {
                "index": index,
                "mid": (candle.high + candle.low) / 2,
                "score": quality_score(
                    upper_wick_percent,
                    body_percent,
                    range_atr,
                    MIN_WICK_PERCENT,
                    MAX_BODY_PERCENT,
                    MIN_CANDLE_RANGE_ATR,
                    not USE_EMA_FILTER or candle.close < current_ema,
                    bearish_reclaim,
                    not REQUIRE_MIDLINE_BREAK
                    or candle.close < (candle.high + candle.low) / 2,
                ),
            }

        buy_confirmed = (
            pending_bull is not None
            and 0 <= index - pending_bull["index"] <= CONFIRMATION_WINDOW
            and (not REQUIRE_BULLISH_BODY or candle.close > candle.open)
            and (not USE_EMA_FILTER or candle.close > current_ema)
            and (
                not REQUIRE_MIDLINE_BREAK
                or candle.close > pending_bull["mid"]
            )
        )
        sell_confirmed = (
            pending_bear is not None
            and 0 <= index - pending_bear["index"] <= CONFIRMATION_WINDOW
            and (not REQUIRE_BEARISH_BODY or candle.close < candle.open)
            and (not USE_EMA_FILTER or candle.close < current_ema)
            and (
                not REQUIRE_MIDLINE_BREAK
                or candle.close < pending_bear["mid"]
            )
        )
        cooldown_passed = (
            last_signal_index is None
            or index - last_signal_index >= SIGNAL_COOLDOWN_BARS
        )

        if active is not None:
            if active.direction == 1 and (
                candle.low <= active.stop or candle.high >= active.target
            ):
                active = None
            elif active.direction == -1 and (
                candle.high >= active.stop or candle.low <= active.target
            ):
                active = None

        signal_direction = 1 if buy_confirmed else -1 if sell_confirmed else 0
        if signal_direction and cooldown_passed and active is None:
            score = (
                pending_bull["score"]
                if signal_direction == 1
                else pending_bear["score"]
            )
            distance = current_atr * STOP_LOSS_ATR_MULTIPLIER
            signal = Signal(
                direction=signal_direction,
                candle=candle,
                score=score,
                entry=candle.close,
                stop=candle.close - distance if signal_direction == 1 else candle.close + distance,
                target=candle.close + distance * TAKE_PROFIT_RR
                if signal_direction == 1
                else candle.close - distance * TAKE_PROFIT_RR,
                reason="bullish liquidity reclaim"
                if signal_direction == 1
                else "bearish liquidity reclaim",
            )
            active = signal
            last_signal_index = index

        if pending_bull is not None and index - pending_bull["index"] >= CONFIRMATION_WINDOW:
            pending_bull = None
        if pending_bear is not None and index - pending_bear["index"] >= CONFIRMATION_WINDOW:
            pending_bear = None

    return ("TRADE ACTIVE" if active is not None else "WAITING"), active


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        return {"symbols": {}}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read state file {STATE_FILE}: {exc}") from exc


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True))
    temporary.replace(STATE_FILE)


def send_pushover(session: requests.Session, symbol: str, signal: Signal) -> None:
    token = os.getenv("PUSHOVER_APP_TOKEN")
    user = os.getenv("PUSHOVER_USER_KEY")
    if not token or not user:
        raise RuntimeError("PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY are required")
    direction = "LONG" if signal.direction == 1 else "SHORT"
    message = (
        f"{symbol} {direction} TRADE | 1H liquidity reclaim\n"
        f"Entry: {signal.entry:.8g}\n"
        f"Stop: {signal.stop:.8g}\n"
        f"Target: {signal.target:.8g}\n"
        f"Quality: {signal.score:.1f}"
    )
    response = session.post(
        PUSHOVER_URL,
        data={"token": token, "user": user, "title": f"{symbol} {direction}", "message": message},
        timeout=20,
    )
    response.raise_for_status()


def run_once(
    session: requests.Session, state: dict[str, Any], symbols: tuple[str, ...]
) -> None:
    for symbol in symbols:
        try:
            candles = fetch_candles(session, symbol)
            signal_state, signal = analyze(candles)
            symbol_state = state.setdefault("symbols", {}).setdefault(symbol, {})
            previous_state = symbol_state.get("signal_state", "WAITING")
            latest_open_time = candles[-1].open_time
            signal_id = (
                f"{latest_open_time}:{signal.direction}"
                if signal is not None and signal.candle.open_time == latest_open_time
                else None
            )
            if (
                signal_state == "TRADE ACTIVE"
                and previous_state == "WAITING"
                and signal is not None
                and signal_id != symbol_state.get("last_alert_id")
            ):
                send_pushover(session, symbol, signal)
                symbol_state["last_alert_id"] = signal_id
                logging.info("%s alert sent: %s", symbol, signal.reason)
            symbol_state["signal_state"] = signal_state
            symbol_state["last_candle_time"] = latest_open_time
            logging.info("%s: %s", symbol, signal_state)
        except Exception:
            logging.exception("Failed to process %s", symbol)
    save_state(state)


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not SYMBOLS:
        raise SystemExit("SYMBOLS must contain at least one Binance symbol")
    session = requests.Session()
    try:
        available_symbols = fetch_futures_symbols(session)
    except requests.RequestException as exc:
        raise SystemExit(f"Cannot load Binance futures symbols: {exc}") from exc

    active_symbols = tuple(symbol for symbol in SYMBOLS if symbol in available_symbols)
    unavailable_symbols = tuple(symbol for symbol in SYMBOLS if symbol not in available_symbols)
    if unavailable_symbols:
        logging.warning(
            "Unavailable USDT perpetual futures (skipping): %s",
            ", ".join(unavailable_symbols),
        )
    if not active_symbols:
        raise SystemExit("None of the configured symbols are available as USDT perpetual futures")

    logging.info(
        "Monitoring %s on closed Binance USDⓈ-M 1H candles",
        ", ".join(active_symbols),
    )
    state = load_state()
    while True:
        run_once(session, state, active_symbols)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
