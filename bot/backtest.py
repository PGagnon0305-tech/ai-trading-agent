from __future__ import annotations

from .config import Config
from .engine import Engine
from .models import Candle


def run(candles: list[Candle], cfg: Config) -> dict:
    e = Engine(cfg)
    for c in candles:
        e.on_candle(c)
    if e.broker.qty != 0 and e.last_close:
        e._close(e.last_close, e.last_ts or 0, "end of data")
    return report(e, cfg)


def report(e: Engine, cfg: Config) -> dict:
    eq = [v for _, v in e.equity_curve] or [cfg.starting_cash]
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    wins = [t.pnl for t in e.trades if t.pnl > 0]
    losses = [-t.pnl for t in e.trades if t.pnl < 0]
    final = e.broker.equity(e.last_close or 0) if e.last_close else cfg.starting_cash
    return {
        "final_equity": round(final, 2),
        "return_pct": round((final / cfg.starting_cash - 1) * 100, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "trades": len(e.trades),
        "win_rate_pct": round(100 * len(wins) / len(e.trades), 1) if e.trades else 0.0,
        "profit_factor": round(sum(wins) / sum(losses), 2) if losses else None,
        "halted": e.risk.s.halted or None,
    }
