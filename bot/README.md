# bot — defensive paper-trading / backtesting engine

Research and education only. **No live order placement is implemented, on purpose.**
Nothing here is "bulletproof" against the market: a strategy can lose money in any code.
What the code does guarantee is that it *fails safe* and cannot exceed its configured risk limits.

```bash
pip install pytest            # only needed for tests; the bot itself has no dependencies
python -m bot backtest                      # synthetic data (a demo, NOT evidence of an edge)
python -m bot backtest --csv candles.csv    # ts,open,high,low,close[,volume]  (ts = unix seconds)
python -m bot --config my.json paper --exchange binance --symbol BTC/USDT --timeframe 1h   # needs `pip install ccxt`
python -m pytest tests
```

## Safety layer
| Protection | Behaviour |
|---|---|
| Position sizing | Risk `risk_per_trade` of equity to the ATR stop; notional capped at `max_leverage` × equity |
| Config validation | Dangerous values (e.g. 50% risk, 20× leverage) and misspelled keys are rejected at startup |
| Daily loss / consecutive losses | New entries blocked until the next UTC day |
| Max drawdown | Flatten and halt permanently; needs a human to delete the state file |
| Kill switch | Create a file named `KILL` (see `kill_switch_file`) → flatten and stop on the next candle |
| Bad data | NaN / inconsistent OHLC / duplicate or out-of-order timestamps / >25% jumps are ignored; 3 in a row halts |
| No lookahead | Signals use closed candles and execute at the *next* open; stops gap-fill at the open |
| Costs | Fees and slippage always applied against you |
| Crash safety | State written atomically (temp file + fsync + rename); corrupt state refuses to start rather than guessing |
| Feed outage | 5 failed polls in a row halts the paper loop |

## Known limits (read before trusting any result)
- Stops fill at the stop price only if the market trades through it; a gap loses more than `risk_per_trade`. Real exchanges can also fail to fill a stop at all.
- Single instrument, single strategy (Donchian breakout + SMA filter). Its default parameters are untuned and **have no demonstrated edge**; synthetic seeds 1 and 7 give -2% and +18%.
- The `paper` poller and `ccxt` data fetch are not covered by tests (they need the network).
- Before ever considering real money: out-of-sample and walk-forward tests on real data, months of paper trading, an exchange-side stop, API keys without withdrawal rights, and an amount you can afford to lose entirely. See `docs/DISCLAIMER.md`.
