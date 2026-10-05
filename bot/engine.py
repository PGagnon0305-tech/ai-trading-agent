from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from .broker import PaperBroker
from .config import Config
from .models import Candle, Trade
from .risk import RiskManager, RiskState
from .strategy import DonchianTrend, Signal

log = logging.getLogger("bot")


class Engine:
    """Processes closed candles one at a time. Used identically by backtest and paper modes."""

    def __init__(self, cfg: Config):
        self.cfg = cfg.validate()
        self.broker = PaperBroker(cfg)
        self.risk = RiskManager(cfg)
        self.strategy = DonchianTrend(cfg)
        self.trades: list[Trade] = []
        self.equity_curve: list[tuple[int, float]] = []
        self.pending: Signal | None = None
        self.last_ts: int | None = None
        self.last_close: float | None = None
        self.bad_streak = 0

    # --- data validation ---------------------------------------------
    def _accept(self, c: Candle) -> bool:
        reason = c.problem()
        if reason is None and self.last_ts is not None and c.ts <= self.last_ts:
            reason = "duplicate or out-of-order timestamp"
        if reason is None and self.last_close and abs(c.close / self.last_close - 1) > self.cfg.max_candle_jump:
            reason = f"close jumped >{self.cfg.max_candle_jump:.0%} from previous close"
        if reason is None:
            self.bad_streak = 0
            return True
        self.bad_streak += 1
        log.warning("rejected candle ts=%s: %s", c.ts, reason)
        if self.bad_streak >= self.cfg.max_consecutive_bad_candles:
            self.risk.halt(f"{self.bad_streak} consecutive bad candles ({reason})")
        return False

    # --- trade plumbing -----------------------------------------------
    def _close(self, ref: float, ts: int, reason: str) -> None:
        b = self.broker
        side, entry_ts, entry_px = b.side, b.entry_ts, b.entry_price
        q, px, pnl = b.close(ref)
        self.trades.append(Trade(side, abs(q), entry_ts, entry_px, ts, px, pnl, reason))
        self.risk.on_trade_closed(pnl)
        log.info("closed %s qty=%.6f @%.4f pnl=%.2f (%s)", side, abs(q), px, pnl, reason)

    def _execute_pending(self, c: Candle) -> None:
        sig, self.pending = self.pending, None
        b = self.broker
        if sig is None:
            return
        if sig.action == "exit":
            if b.qty != 0:
                self._close(c.open, c.ts, "signal")
            return
        if b.qty != 0 or self.risk.entry_blocked():
            return
        equity = b.equity(c.open)
        qty = self.risk.size(equity, c.open, sig.stop_distance)
        if qty <= 0:
            return
        sign = 1 if sig.action == "long" else -1
        b.open(sign * qty, c.open, c.open - sign * sig.stop_distance, c.ts)
        log.info("opened %s qty=%.6f @%.4f stop=%.4f", sig.action, qty, b.entry_price, b.stop)

    # --- main entry point -----------------------------------------------
    def on_candle(self, c: Candle) -> None:
        if not self._accept(c):
            self._guard(c.close if c.problem() is None else (self.last_close or 0.0), c.ts if c.problem() is None else None)
            return
        self.last_ts, self.last_close = c.ts, c.close
        b = self.broker

        if self.pending and not self.risk.must_flatten():
            self._execute_pending(c)
        self.pending = None

        if b.qty != 0:
            ref = b.stop_fill_ref(c)
            if ref is not None:
                self._close(ref, c.ts, "stop")

        equity = b.equity(c.close)
        self.risk.on_equity(equity, c.ts)
        self.equity_curve.append((c.ts, equity))

        flatten = self.risk.must_flatten()
        if flatten:
            if b.qty != 0:
                self._close(c.close, c.ts, f"flatten: {flatten}")
            self.strategy.warmup(c)
            return

        side = b.side
        sig = self.strategy.update(c, side)
        if side and self.strategy.atr:
            d = self.cfg.atr_stop_mult * self.strategy.atr
            b.trail(c.close - d if side == "long" else c.close + d)
        self.pending = sig

    def _guard(self, price: float, ts: int | None) -> None:
        """Bad data: if it has made us halt, flatten at the last known good price."""
        flatten = self.risk.must_flatten()
        if flatten and self.broker.qty != 0 and price > 0:
            self._close(price, ts or self.last_ts or 0, f"flatten: {flatten}")

    def warmup(self, candles: list[Candle]) -> None:
        """Feed history to the strategy without trading (fresh start, or rebuilding indicators after a restart)."""
        for c in candles:
            if c.problem() is None:
                self.strategy.warmup(c)
                if self.last_ts is None or c.ts >= self.last_ts:
                    self.last_ts, self.last_close = c.ts, c.close

    # --- persistence ------------------------------------------------------
    def snapshot(self) -> dict:
        b = self.broker
        return {
            "broker": {k: getattr(b, k) for k in ("cash", "qty", "entry_price", "entry_ts", "entry_fee", "stop")},
            "risk": self.risk.snapshot(),
            "last_ts": self.last_ts,
            "last_close": self.last_close,
            "pending": None if self.pending is None else vars(self.pending),
        }

    def restore(self, snap: dict) -> None:
        for k, v in snap["broker"].items():
            setattr(self.broker, k, v)
        self.risk = RiskManager(self.cfg, RiskState(**snap["risk"]))
        self.last_ts, self.last_close = snap["last_ts"], snap["last_close"]
        self.pending = Signal(**snap["pending"]) if snap.get("pending") else None

    def save(self, path: str) -> None:
        """Atomic write: a crash mid-save can never leave a truncated state file."""
        p = Path(path)
        fd, tmp = tempfile.mkstemp(dir=p.parent or ".", prefix=p.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.snapshot(), f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def load(self, path: str) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        self.restore(json.loads(p.read_text()))  # corrupt state raises: fail closed, never guess
        return True
