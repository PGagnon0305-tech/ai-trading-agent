from __future__ import annotations

from .config import Config
from .models import Candle


class PaperBroker:
    """Single-instrument simulated account. Costs always work against us."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cash = cfg.starting_cash
        self.qty = 0.0  # signed: >0 long, <0 short
        self.entry_price = 0.0
        self.entry_ts = 0
        self.entry_fee = 0.0
        self.stop = 0.0

    @property
    def side(self) -> str | None:
        return "long" if self.qty > 0 else "short" if self.qty < 0 else None

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    def _fill(self, signed_qty: float, ref: float) -> tuple[float, float]:
        px = ref * (1 + self.cfg.slippage * (1 if signed_qty > 0 else -1))
        fee = abs(signed_qty) * px * self.cfg.fee_rate
        self.cash -= signed_qty * px + fee
        return px, fee

    def open(self, signed_qty: float, ref: float, stop: float, ts: int) -> None:
        assert self.qty == 0 and signed_qty != 0
        px, fee = self._fill(signed_qty, ref)
        self.qty, self.entry_price, self.entry_ts, self.entry_fee, self.stop = signed_qty, px, ts, fee, stop

    def close(self, ref: float) -> tuple[float, float, float]:
        """Flatten. Returns (side-qty, exit_price, net_pnl)."""
        assert self.qty != 0
        q = self.qty
        px, fee = self._fill(-q, ref)
        pnl = (px - self.entry_price) * q - self.entry_fee - fee
        self.qty, self.stop, self.entry_fee = 0.0, 0.0, 0.0
        return q, px, pnl

    def stop_fill_ref(self, c: Candle) -> float | None:
        """Reference price if the stop was hit inside this candle (gaps fill at the open)."""
        if self.qty > 0 and c.low <= self.stop:
            return min(self.stop, c.open)
        if self.qty < 0 and c.high >= self.stop:
            return max(self.stop, c.open)
        return None

    def trail(self, new_stop: float) -> None:
        """Stops only ever move in the position's favour."""
        if self.qty > 0:
            self.stop = max(self.stop, new_stop)
        elif self.qty < 0:
            self.stop = min(self.stop, new_stop)
