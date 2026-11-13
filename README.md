# Binance 1H liquidity alerts

This is a small Debian service that reads **closed 1-hour USDⓈ-M perpetual futures candles** from Binance, applies the useful parts of the supplied TradingView indicator, and sends a Pushover message when `WAITING` changes to `TRADE ACTIVE`.

It monitors the symbols in `SYMBOLS`. It does not place orders.

## Run directly as root

Place `.env` beside the files. The launcher loads all `.env` settings before starting Python. On the first run, it creates `.env` from `.env.example`; edit the Pushover credentials and run it again:

```bash
chmod +x install_and_run.bash
./install_and_run.bash
```

Check logs:

```bash
./install_and_run.bash 2>&1 | tee -a liquidity-alert.log
```

Test Pushover without waiting for a trading signal:

```bash
chmod +x test_notification.bash
./test_notification.bash
```

## Start and stop in the background

After `.env` is configured:

```bash
chmod +x service.bash
./service.bash start
./service.bash status
./service.bash stop
```

Other commands:

```bash
./service.bash restart
tail -f liquidity-alert.log
```

The PID is stored in `liquidity-alert.pid`. The process can also be stopped with `Ctrl-C` when running `install_and_run.bash` directly in the foreground.

Create a Pushover application to obtain `PUSHOVER_APP_TOKEN`, then put that token and your Pushover user key in `.env`. Binance public futures klines do not require API keys.

Before every polling session, the service checks Binance `exchangeInfo` and keeps configured symbols that are currently trading USDT futures. For newer TradFi/stock contracts that are not returned in the standard metadata list, it also verifies the symbol directly through the futures klines endpoint. Invalid, delisted, or unsupported symbols are printed as warnings and skipped for that session; they are checked again during the next session.

The JSON state file prevents duplicate alerts across polling cycles and restarts. The service treats the final Binance kline as incomplete and never signals on it.

Pushover alerts are sent only from Monday 07:00 through Saturday 00:00 in UTC+7. The service checks Binance's external futures server-time endpoint for this schedule instead of using the local server clock. If that time check fails, the alert is suppressed.
