from __future__ import annotations

import csv
import math
import random

from .models import Candle


def load_csv(path: str) -> list[Candle]:
    """CSV columns: ts,open,high,low,close[,volume]  (ts in unix seconds)."""
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out.append(Candle(int(float(row["ts"])), float(row["open"]), float(row["high"]),
                              float(row["low"]), float(row["close"]), float(row.get("volume") or 0)))
    return out


def synthetic(n: int = 5000, seed: int = 1, start: float = 100.0, step: int = 3600) -> list[Candle]:
    """Regime-switching random walk, for demos and tests (not evidence of an edge)."""
    rng = random.Random(seed)
    px, drift, out = start, 0.0, []
    for i in range(n):
        if i % 400 == 0:
            drift = rng.choice([-0.0004, 0.0, 0.0006])
        o = px * (1 + rng.gauss(0, 0.002))  # small overnight gap
        r = rng.gauss(drift, 0.01)
        c = max(o * (1 + r), 0.01)
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.004)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.004)))
        out.append(Candle(1_700_000_000 + i * step, o, h, lo, c, rng.uniform(100, 1000)))
        px = c
    return out


def fetch_ccxt(exchange: str, symbol: str, timeframe: str, limit: int = 300) -> list[Candle]:
    """Closed candles only (the still-forming last candle is dropped). Needs `pip install ccxt`."""
    import ccxt  # optional dependency

    ex = getattr(ccxt, exchange)({"enableRateLimit": True})
    rows = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
    return [Candle(int(r[0] // 1000), *map(float, r[1:6])) for r in rows[:-1]]
