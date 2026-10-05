from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .config import Config
from .models import Candle


@dataclass(frozen=True)
class Signal:
    action: str  # "long" | "short" | "exit"
    stop_distance: float = 0.0


class DonchianTrend:
    """Donchian breakout with an SMA trend filter and ATR stop distance.

    Signals are computed on a *closed* candle; the engine executes them at the
    next candle's open, so there is no lookahead.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.highs: deque[float] = deque(maxlen=cfg.entry_lookback)
        self.lows: deque[float] = deque(maxlen=max(cfg.entry_lookback, cfg.exit_lookback))
        self.closes: deque[float] = deque(maxlen=cfg.trend_lookback)
        self.atr: float | None = None
        self._tr_seed: list[float] = []
        self._prev_close: float | None = None

    @property
    def ready(self) -> bool:
        c = self.cfg
        return (
            self.atr is not None
            and len(self.highs) >= c.entry_lookback
            and len(self.lows) >= max(c.entry_lookback, c.exit_lookback)
            and len(self.closes) >= c.trend_lookback
        )

    def _update_atr(self, c: Candle) -> None:
        n = self.cfg.atr_period
        pc = self._prev_close
        tr = c.high - c.low if pc is None else max(c.high - c.low, abs(c.high - pc), abs(c.low - pc))
        if self.atr is None:
            self._tr_seed.append(tr)
            if len(self._tr_seed) >= n:
                self.atr = sum(self._tr_seed) / n
        else:
            self.atr = (self.atr * (n - 1) + tr) / n

    def update(self, c: Candle, side: str | None) -> Signal | None:
        sig = None
        if self.ready and self.atr and self.atr > 0:
            hi = max(self.highs)
            lo_entry = min(self.lows)
            lo_exit = min(list(self.lows)[-self.cfg.exit_lookback :])
            hi_exit = max(list(self.highs)[-self.cfg.exit_lookback :])
            trend = sum(self.closes) / len(self.closes)
            stop = self.cfg.atr_stop_mult * self.atr
            if side == "long":
                if c.close < lo_exit:
                    sig = Signal("exit")
            elif side == "short":
                if c.close > hi_exit:
                    sig = Signal("exit")
            else:
                if c.close > hi and c.close > trend:
                    sig = Signal("long", stop)
                elif self.cfg.allow_short and c.close < lo_entry and c.close < trend:
                    sig = Signal("short", stop)
        # update windows only after the decision so the breakout level excludes this bar
        self.highs.append(c.high)
        self.lows.append(c.low)
        self.closes.append(c.close)
        self._update_atr(c)
        self._prev_close = c.close
        return sig

    def warmup(self, c: Candle) -> None:
        self.update(c, None)
