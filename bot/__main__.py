from __future__ import annotations

import argparse
import json
import logging
import time

from . import backtest, data
from .config import Config
from .engine import Engine


def main() -> None:
    ap = argparse.ArgumentParser(prog="bot", description="Paper-trading/backtest bot (no live orders).")
    ap.add_argument("--config", help="JSON config file (unknown keys are rejected)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    bt = sub.add_parser("backtest")
    bt.add_argument("--csv")
    bt.add_argument("--seed", type=int, default=1, help="synthetic data seed when no --csv")
    pp = sub.add_parser("paper", help="poll an exchange for closed candles and paper trade")
    pp.add_argument("--exchange", default="binance")
    pp.add_argument("--symbol", default="BTC/USDT")
    pp.add_argument("--timeframe", default="1h")
    pp.add_argument("--poll", type=int, default=60, help="seconds between polls")
    a = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if a.verbose else (logging.WARNING if a.cmd == 'backtest' else logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.load(a.config)

    if a.cmd == "backtest":
        candles = data.load_csv(a.csv) if a.csv else data.synthetic(seed=a.seed)
        print(json.dumps(backtest.run(candles, cfg), indent=2))
        return

    e = Engine(cfg)
    resumed = e.load(cfg.state_file)
    logging.info("paper mode, state %s", "resumed" if resumed else "fresh")
    errors = 0
    while True:
        try:
            candles = data.fetch_ccxt(a.exchange, a.symbol, a.timeframe)
            if not e.strategy.ready:
                # indicators are not persisted: rebuild them from history up to the last candle we
                # already traded (or all but the newest, when fresh); later candles go through on_candle
                # so a restored position's stop is checked against anything missed while we were down
                cut = e.last_ts if resumed and e.last_ts else candles[-2].ts
                e.warmup([c for c in candles if c.ts <= cut])
            for c in candles:
                if e.last_ts is None or c.ts > e.last_ts:
                    e.on_candle(c)
            e.save(cfg.state_file)
            errors = 0
        except Exception:  # network/exchange failure: back off, never trade blind
            errors += 1
            logging.exception("poll failed (%d in a row)", errors)
            if errors >= 5:
                e.risk.halt("feed unavailable: 5 consecutive failures")
                e.save(cfg.state_file)
        if e.risk.must_flatten() and e.broker.qty == 0:
            logging.error("halted: %s - stopping. Delete state file/KILL file to restart.", e.risk.must_flatten())
            return
        time.sleep(a.poll)


if __name__ == "__main__":
    main()
