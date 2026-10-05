from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class Config:
    # --- strategy ---
    entry_lookback: int = 20
    exit_lookback: int = 10
    trend_lookback: int = 100
    atr_period: int = 14
    atr_stop_mult: float = 3.0
    allow_short: bool = False
    # --- risk (hard limits; the engine cannot exceed these) ---
    risk_per_trade: float = 0.005  # fraction of equity lost if the stop fills at its price
    max_leverage: float = 1.0  # notional / equity cap
    max_daily_loss: float = 0.02  # blocks new entries for the rest of the UTC day
    max_drawdown: float = 0.10  # peak-to-trough: flatten and halt until manually reset
    max_consecutive_losses: int = 4  # blocks new entries for the rest of the UTC day
    # --- data sanity ---
    max_candle_jump: float = 0.25  # reject a close that moves >25% from the previous close
    max_consecutive_bad_candles: int = 3  # then halt: the feed is untrustworthy
    # --- execution model ---
    fee_rate: float = 0.001
    slippage: float = 0.0005
    qty_step: float = 1e-6
    min_notional: float = 10.0
    # --- account / ops ---
    starting_cash: float = 10_000.0
    kill_switch_file: str = "KILL"  # create this file to flatten and halt immediately
    state_file: str = "state.json"

    def validate(self) -> "Config":
        def need(ok: bool, msg: str) -> None:
            if not ok:
                raise ValueError(f"invalid config: {msg}")

        need(0 < self.risk_per_trade <= 0.02, "risk_per_trade must be in (0, 0.02]")
        need(0 < self.max_leverage <= 3, "max_leverage must be in (0, 3]")
        need(0 < self.max_daily_loss <= 0.2, "max_daily_loss must be in (0, 0.2]")
        need(0 < self.max_drawdown <= 0.5, "max_drawdown must be in (0, 0.5]")
        need(self.max_daily_loss <= self.max_drawdown, "max_daily_loss must not exceed max_drawdown")
        need(self.max_consecutive_losses >= 1, "max_consecutive_losses must be >= 1")
        need(self.atr_stop_mult > 0, "atr_stop_mult must be > 0")
        need(min(self.entry_lookback, self.exit_lookback, self.trend_lookback, self.atr_period) >= 2, "lookbacks must be >= 2")
        need(0 <= self.fee_rate < 0.05 and 0 <= self.slippage < 0.05, "fee_rate/slippage out of range")
        need(self.starting_cash > 0 and self.qty_step > 0 and self.min_notional >= 0, "cash/step/notional invalid")
        need(0 < self.max_candle_jump < 1 and self.max_consecutive_bad_candles >= 1, "data sanity limits invalid")
        return self

    @classmethod
    def load(cls, path: str | None) -> "Config":
        if not path:
            return cls().validate()
        raw = json.loads(Path(path).read_text())
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:  # a typo in a risk setting must not be silently ignored
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        return cls(**raw).validate()

    def to_dict(self) -> dict:
        return asdict(self)
