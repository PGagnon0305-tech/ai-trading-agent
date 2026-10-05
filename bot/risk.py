from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Config


@dataclass
class RiskState:
    peak: float = 0.0
    day: int = -1
    day_start_equity: float = 0.0
    consecutive_losses: int = 0
    day_blocked: str = ""  # non-empty: no new entries until next UTC day
    halted: str = ""  # non-empty: flatten and stay out until a human resets state


class RiskManager:
    def __init__(self, cfg: Config, state: RiskState | None = None):
        self.cfg = cfg
        self.s = state or RiskState()

    # --- state updates -------------------------------------------------
    def on_equity(self, equity: float, ts: int) -> None:
        s, c = self.s, self.cfg
        if not math.isfinite(equity) or equity <= 0:
            s.halted = s.halted or f"equity invalid/depleted ({equity})"
            return
        day = ts // 86400
        if day != s.day:
            s.day, s.day_start_equity = day, equity
            s.day_blocked, s.consecutive_losses = "", 0
        s.peak = max(s.peak, equity)
        if s.peak > 0 and (s.peak - equity) / s.peak >= c.max_drawdown:
            s.halted = s.halted or f"max drawdown {c.max_drawdown:.0%} hit"
        if s.day_start_equity > 0 and (s.day_start_equity - equity) / s.day_start_equity >= c.max_daily_loss:
            s.day_blocked = s.day_blocked or f"daily loss {c.max_daily_loss:.0%} hit"

    def on_trade_closed(self, pnl: float) -> None:
        s = self.s
        s.consecutive_losses = s.consecutive_losses + 1 if pnl < 0 else 0
        if s.consecutive_losses >= self.cfg.max_consecutive_losses:
            s.day_blocked = s.day_blocked or f"{s.consecutive_losses} consecutive losses"

    def halt(self, reason: str) -> None:
        self.s.halted = self.s.halted or reason

    # --- decisions -----------------------------------------------------
    def kill_switch_on(self) -> bool:
        return Path(self.cfg.kill_switch_file).exists()

    def must_flatten(self) -> str:
        if self.kill_switch_on():
            return "kill switch file present"
        return self.s.halted

    def entry_blocked(self) -> str:
        return self.must_flatten() or self.s.day_blocked

    def size(self, equity: float, price: float, stop_distance: float) -> float:
        """Quantity such that a stop fill at its price loses ~risk_per_trade, capped by leverage."""
        c = self.cfg
        if not (math.isfinite(equity) and math.isfinite(price) and math.isfinite(stop_distance)):
            return 0.0
        if equity <= 0 or price <= 0 or stop_distance <= 0:
            return 0.0
        qty = min(equity * c.risk_per_trade / stop_distance, equity * c.max_leverage / price)
        qty = math.floor(qty / c.qty_step) * c.qty_step
        if qty * price < c.min_notional:
            return 0.0
        return qty

    def snapshot(self) -> dict:
        return asdict(self.s)
