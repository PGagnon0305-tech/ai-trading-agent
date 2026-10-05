from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Candle:
    ts: int  # unix seconds, candle open time
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def problem(self) -> str | None:
        """Return a description of what is wrong with this candle, or None if sane."""
        vals = (self.open, self.high, self.low, self.close, self.volume)
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals):
            return "non-finite value"
        if min(self.open, self.high, self.low, self.close) <= 0:
            return "non-positive price"
        if self.volume < 0:
            return "negative volume"
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            return "inconsistent OHLC"
        return None


@dataclass
class Trade:
    side: str  # "long" | "short"
    qty: float
    entry_ts: int
    entry_price: float
    exit_ts: int
    exit_price: float
    pnl: float  # net of fees
    reason: str
