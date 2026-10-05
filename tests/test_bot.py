import json
import math
import os

import pytest

from bot import backtest, data
from bot.config import Config
from bot.engine import Engine
from bot.models import Candle
from bot.risk import RiskManager


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # kill-switch / state files live in a scratch dir


def C(ts, o, h=None, l=None, c=None):
    c = o if c is None else c
    return Candle(ts, o, h if h is not None else max(o, c), l if l is not None else min(o, c), c, 1.0)


def small_cfg(**kw):
    base = dict(entry_lookback=5, exit_lookback=3, trend_lookback=5, atr_period=3, atr_stop_mult=2.0, min_notional=1.0)
    base.update(kw)
    return Config(**base).validate()


def rising(n, start=100.0, step=1.0, t0=1_700_000_000):
    return [C(t0 + i * 3600, start + i * step, start + i * step + 0.6, start + i * step - 0.6, start + i * step + 0.5) for i in range(n)]


# ---------- config ----------
def test_config_rejects_dangerous_values():
    for bad in (dict(risk_per_trade=0.5), dict(max_leverage=20), dict(max_drawdown=0.9), dict(max_daily_loss=0.3, max_drawdown=0.2)):
        with pytest.raises(ValueError):
            Config(**bad).validate()


def test_config_rejects_unknown_keys(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"risk_per_tade": 0.01}))
    with pytest.raises(ValueError):
        Config.load(str(p))


# ---------- data validation ----------
@pytest.mark.parametrize("c", [
    Candle(1, float("nan"), 1, 1, 1), Candle(1, -1, 1, 1, 1), Candle(1, 10, 9, 8, 9), Candle(1, 10, 11, 9, 10, -1),
    Candle(1, 10, 11, 9, float("inf")),
])
def test_bad_candles_flagged(c):
    assert c.problem()


def test_bad_candles_ignored_then_halt():
    e = Engine(small_cfg())
    e.on_candle(C(1000, 100))
    e.on_candle(Candle(2000, float("nan"), 1, 1, 1))
    assert not e.risk.s.halted and e.last_ts == 1000
    e.on_candle(C(3000, 200))  # +100% jump
    e.on_candle(Candle(4000, 5, 1, 1, 1))
    assert "bad candles" in e.risk.s.halted


def test_duplicate_and_out_of_order_ignored():
    e = Engine(small_cfg())
    e.on_candle(C(1000, 100))
    e.on_candle(C(1000, 101))
    e.on_candle(C(900, 101))
    assert len(e.equity_curve) == 1


# ---------- sizing ----------
def test_size_is_capped_by_leverage_and_rejects_garbage():
    r = RiskManager(small_cfg(max_leverage=1.0, risk_per_trade=0.02))
    assert r.size(10_000, 100, 0.01) * 100 <= 10_000 + 1e-6  # tiny stop must not create huge size
    for bad in ((0, 100, 1), (10_000, 0, 1), (10_000, 100, 0), (float("nan"), 100, 1), (10_000, 100, float("inf")), (-5, 100, 1)):
        assert r.size(*bad) == 0.0


def test_risk_sizing_math():
    r = RiskManager(small_cfg(risk_per_trade=0.01, max_leverage=3))
    assert r.size(10_000, 100, 5) == pytest.approx(20.0, abs=1e-5)  # $100 risk / $5 stop


# ---------- execution realism ----------
def test_signal_executes_next_open_not_same_bar():
    cfg = small_cfg()
    e = Engine(cfg)
    bars = rising(30)
    for c in bars:
        e.on_candle(c)
    assert e.trades or e.broker.qty > 0
    entry_ts = e.broker.entry_ts if e.broker.qty else e.trades[0].entry_ts
    assert entry_ts > bars[cfg.trend_lookback].ts  # never on the first bar a signal could exist


def test_gap_through_stop_fills_at_open_not_stop():
    e = Engine(small_cfg(slippage=0.0, fee_rate=0.0))
    for c in rising(30):
        e.on_candle(c)
    assert e.broker.qty > 0
    stop, last = e.broker.stop, e.last_close
    gap = stop * 0.8
    e.on_candle(C(e.last_ts + 3600, gap, gap * 1.01, gap * 0.99, gap))
    t = e.trades[-1]
    assert t.reason == "stop" and t.exit_price == pytest.approx(gap)  # worse than the stop price


def test_trailing_stop_never_loosens():
    e = Engine(small_cfg())
    stops = []
    for c in rising(25) + [C(1_700_100_000 + i * 3600, 112 - i * 0.3) for i in range(6)]:
        e.on_candle(c)
        if e.broker.qty > 0:
            stops.append(e.broker.stop)
    assert stops == sorted(stops)


# ---------- kill switches ----------
def test_kill_switch_file_flattens_and_blocks():
    e = Engine(small_cfg())
    bars = rising(60)
    for c in bars[:30]:
        e.on_candle(c)
    assert e.broker.qty > 0
    open("KILL", "w").close()
    e.on_candle(bars[30])
    assert e.broker.qty == 0 and "kill switch" in e.trades[-1].reason
    n = len(e.trades)
    for c in bars[31:]:
        e.on_candle(c)
    assert e.broker.qty == 0 and len(e.trades) == n


def test_drawdown_halt_is_sticky():
    e = Engine(small_cfg(max_drawdown=0.01, max_daily_loss=0.01, risk_per_trade=0.02))
    bars = rising(30) + [C(1_700_200_000 + i * 3600, 130 - i * 4) for i in range(20)]
    for c in bars:
        e.on_candle(c)
    assert e.risk.s.halted and e.broker.qty == 0
    for c in rising(40, start=50, t0=1_700_900_000):
        e.on_candle(c)
    assert e.broker.qty == 0  # recovery does not un-halt


# ---------- persistence ----------
def test_state_roundtrip_matches_uninterrupted_run():
    cfg = small_cfg()
    bars = data.synthetic(600, seed=3)
    full = Engine(cfg)
    for c in bars:
        full.on_candle(c)

    a = Engine(cfg)
    for c in bars[:300]:
        a.on_candle(c)
    a.save("s.json")
    b = Engine(cfg)
    assert b.load("s.json")
    b.warmup([c for c in bars if c.ts <= b.last_ts])
    for c in bars[300:]:
        b.on_candle(c)
    assert b.broker.equity(b.last_close) == pytest.approx(full.broker.equity(full.last_close), rel=1e-9)


def test_corrupt_state_fails_closed():
    open("s.json", "w").write("{not json")
    with pytest.raises(Exception):
        Engine(small_cfg()).load("s.json")


def test_atomic_save_leaves_no_temp_files():
    e = Engine(small_cfg())
    e.on_candle(C(1000, 100))
    e.save("s.json")
    assert os.listdir(".") == ["s.json"]


# ---------- fuzz: invariants hold on hostile data ----------
@pytest.mark.parametrize("seed", range(25))
def test_invariants_under_hostile_data(seed):
    import random

    rng = random.Random(seed)
    cfg = Config(risk_per_trade=0.01, max_leverage=1.0, max_drawdown=0.10, max_daily_loss=0.03).validate()
    e = Engine(cfg)
    px = 100.0
    peak = cfg.starting_cash
    for i, c in enumerate(data.synthetic(3000, seed=seed)):
        r = rng.random()
        if r < 0.01:
            c = Candle(c.ts, float("nan"), c.high, c.low, c.close, 1)  # garbage tick
        elif r < 0.02:
            c = Candle(c.ts, c.open * 3, c.high * 3, c.low * 3, c.close * 3, 1)  # fat-finger spike
        elif r < 0.03:
            c = Candle(c.ts - 7200, c.open, c.high, c.low, c.close, 1)  # stale/out-of-order
        e.on_candle(c)
        if e.last_close:
            eq = e.broker.equity(e.last_close)
            assert math.isfinite(eq) and eq > 0
            assert abs(e.broker.qty) * e.last_close <= eq * cfg.max_leverage * 1.5 + 1e-6  # price drift between entry/mark
        peak = max(peak, e.broker.equity(e.last_close or 0) if e.last_close else peak)
    for t in e.trades:
        assert t.pnl > -0.04 * cfg.starting_cash  # 1% risk + costs + gap, never a blow-up
    # max drawdown stays near the configured limit (overshoot = one stopped trade + gap)
    eqs = [v for _, v in e.equity_curve]
    p, worst = eqs[0], 0.0
    for v in eqs:
        p = max(p, v)
        worst = max(worst, (p - v) / p)
    assert worst < cfg.max_drawdown + 0.05


def test_backtest_report_shape():
    rep = backtest.run(data.synthetic(2000, seed=2), Config())
    assert {"final_equity", "max_drawdown_pct", "trades", "profit_factor"} <= set(rep)


# ---------- paper loop replay with a fake feed ----------
from bot.__main__ import run_paper  # noqa: E402


class FakeFeed:
    """Serves the last 300 closed candles as of a moving 'now', like ccxt would."""

    def __init__(self, candles, start=200, step=1):
        self.candles, self.now, self.step = candles, start, step
        self.fail = 0

    def __call__(self):
        if self.fail:
            self.fail -= 1
            raise ConnectionError("simulated outage")
        self.now = min(self.now + self.step, len(self.candles))
        return self.candles[max(0, self.now - 300): self.now]


def test_paper_loop_matches_backtest():
    cfg = Config().validate()
    bars = data.synthetic(1500, seed=7)
    feed = FakeFeed(bars, start=300)
    e = run_paper(cfg, feed, 0, sleep=lambda s: None, max_polls=len(bars) - 300 + 1)
    ref = Engine(cfg)
    for c in bars:
        ref.on_candle(c)
    assert e.last_ts == bars[-1].ts
    assert e.broker.equity(e.last_close) == pytest.approx(ref.broker.equity(ref.last_close), rel=0.05)
    assert e.trades  # it actually traded


def test_paper_loop_restart_resumes_state():
    cfg = Config().validate()
    bars = data.synthetic(1500, seed=7)
    feed = FakeFeed(bars, start=300)
    run_paper(cfg, feed, 0, sleep=lambda s: None, max_polls=400)  # "crash" here
    saved = json.load(open(cfg.state_file))
    feed.now += 25  # 25 candles pass while the bot is down
    e2 = run_paper(cfg, feed, 0, sleep=lambda s: None, max_polls=1)
    assert e2.last_ts == bars[feed.now - 1].ts
    assert e2.strategy.ready
    assert e2.risk.s.peak >= saved["risk"]["peak"]


def test_paper_loop_outage_halts_after_five_failures():
    cfg = Config().validate()
    feed = FakeFeed(data.synthetic(600, seed=2), start=300)
    run_paper(cfg, feed, 0, sleep=lambda s: None, max_polls=3)
    feed.fail = 10
    e = run_paper(cfg, feed, 0, sleep=lambda s: None, max_polls=10)
    assert "feed unavailable" in e.risk.s.halted
    assert json.load(open(cfg.state_file))["risk"]["halted"]


def test_paper_loop_survives_single_blip_and_garbage():
    cfg = Config().validate()
    bars = data.synthetic(800, seed=4)
    feed = FakeFeed(bars, start=300)
    run_paper(cfg, feed, 0, sleep=lambda s: None, max_polls=5)
    feed.fail = 2
    feed.candles = list(feed.candles)
    feed.candles[400] = Candle(bars[400].ts, float("nan"), 1, 1, 1)
    e = run_paper(cfg, feed, 0, sleep=lambda s: None, max_polls=100)
    assert not e.risk.s.halted or "bad candles" not in e.risk.s.halted


def test_paper_loop_stops_on_kill_switch():
    cfg = Config().validate()
    feed = FakeFeed(data.synthetic(900, seed=7), start=300)
    run_paper(cfg, feed, 0, sleep=lambda s: None, max_polls=150)
    open("KILL", "w").close()
    e = run_paper(cfg, feed, 0, sleep=lambda s: None, max_polls=200)
    assert e.broker.qty == 0 and e.last_ts < feed.candles[-1].ts  # stopped early
