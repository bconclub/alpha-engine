# Alpha Trade Signals — Complete Reference

How the bot decides when to enter, how it manages positions, and when it exits.

---

## 11-Signal Arsenal

Every 5 seconds the bot computes all 11 indicators from 30 x 1m candles. Each signal fires independently as bullish or bearish. Entry requires **3 of 4 signals minimum** (no coin-flip 2/4 entries).

### Core 4 Signals

| # | Signal | Tag | Long Condition | Short Condition |
|---|--------|-----|---------------|-----------------|
| 1 | **Momentum 60s** | MOM | momentum_60s >= +0.08% | momentum_60s <= -0.08% |
| 2 | **Volume Spike** | VOL | vol_ratio >= 0.8x (vs 10-candle avg) | same |
| 3 | **RSI Extremes** | RSI | RSI(14) < 35 | RSI(14) > 65 |
| 4 | **Bollinger Band** | BB | price_position <= 0.15 (bottom 15%) | price_position >= 0.85 (top 15%) |

### Bonus 4 Signals

| # | Signal | Tag | Condition |
|---|--------|-----|-----------|
| 5 | **Momentum 5m** | MOM5m | \|momentum_300s\| >= 0.30% — catches slow sustained moves |
| 6 | **Trend Continuation** | TCONT | New 15-candle high/low + volume >= 1.0x average |
| 7 | **VWAP Reclaim + EMA** | VWAP | Long: price > VWAP(30) AND EMA(9) > EMA(21). Short: inverse |
| 8 | **BB Squeeze Breakout** | BBSQZ | BB inside Keltner Channel (squeeze) + price breaks out with volume |

### Specialist 3 Signals

| # | Signal | Tag | Condition |
|---|--------|-----|-----------|
| 9 | **Liquidity Sweep** | LIQSWEEP | Sweep past swing H/L (12 candles) then reclaim + RSI divergence |
| 10 | **Fair Value Gap** | FVG | 3-candle imbalance gap >= 0.05%, price fills the gap |
| 11 | **Volume Divergence** | VOLDIV | Price up but volume down 20% (hollow pump) or price down but volume down 20% (exhausted sellers) |

---

## Entry Gate

```
Minimum: 3/4 signals aligned in same direction
Exception: RSI < 30 or RSI > 70 = immediate entry (RSI Override)
2nd position: requires 3/4+
Post-streak: first trade back after 3 consecutive losses requires 3/4
```

### Per-Pair Signal Strength

| Pair | Min Signals | Allocation |
|------|-------------|------------|
| XRP | 3/4 | 50% (best performer) |
| ETH | 3/4 | 30% (mixed) |
| BTC | 3/4 | 20% (lowest win rate) |

### Idle Threshold Widening

After 30+ minutes with no entry, thresholds loosen by 20%:
- Momentum: 0.08% -> 0.064%
- Volume: 0.8x -> 0.64x
- RSI range widens

---

## Setup Classification

The combination of signals that fire determines the setup type badge:

| Setup Type | Trigger |
|------------|---------|
| `RSI_OVERRIDE` | RSI < 30 or RSI > 70 (immediate entry) |
| `MOMENTUM_BURST` | MOM + VOL signals |
| `MEAN_REVERT` | BB + RSI signals (band bounce) |
| `VWAP_RECLAIM` | VWAP signal active |
| `TREND_CONT` | TCONT signal active |
| `BB_SQUEEZE` | BBSQZ signal (squeeze breakout) |
| `LIQ_SWEEP` | LIQSWEEP signal (liquidity sweep) |
| `FVG_FILL` | FVG signal (fair value gap) |
| `VOL_DIVERGENCE` | VOLDIV signal (volume divergence) |
| `MULTI_SIGNAL` | 4+ signals fired simultaneously |
| `MIXED` | Default fallback |

---

## 3-Phase Exit System

### Phase 1: Hands-Off (0-30 seconds)

Only hard stop loss fires. Protects against fill slippage.

Exception: If peak PnL >= +0.5%, skip immediately to Phase 2.

**Stop Loss Distances (per-pair floor):**

| Pair | SL Floor | Cap |
|------|----------|-----|
| BTC | 0.40% | 1.5% (futures) |
| ETH | 0.45% | 1.5% (futures) |
| XRP | 0.50% | 1.5% (futures) |
| Spot | 2.0% | 3.0% (spot) |

Dynamic SL: `max(pair_floor, ATR_14 * 1.5)`

### Phase 2: Watch & Trail (30s - 10 minutes)

| Exit Type | Trigger | Color |
|-----------|---------|-------|
| **Trailing Stop** | Peak PnL >= +0.15% activates trail | Green |
| **Breakeven** | Peak >= +0.20% AND price returns to entry | Gray |
| **Hard TP** | Capital PnL >= 10% (runaway winner safety) | Green |
| **Profit Pullback** | Peak >= +0.50% AND 30% retracement from peak | Yellow |
| **Profit Decay** | Peak >= +0.30% AND current < +0.10% | Yellow |
| **Signal Reversal** | In profit >= +0.30% AND RSI flips or momentum reverses | Yellow |

### Phase 3: Trail or Cut (10 - 30 minutes)

| Exit Type | Trigger | Color |
|-----------|---------|-------|
| **Trailing** | Continues from Phase 2 | Green |
| **Flatline** | 10+ min hold AND \|PnL\| < 0.05% (dead momentum) | Gray |
| **Timeout** | 30 min hold AND not trailing | Gray |
| **Safety** | 30 min hold AND PnL < 0% (cut losers) | Gray |

---

## Trailing Stop Tiers

Trail activates at +0.15% peak PnL. Distance widens as profit grows (never tightens):

| Peak PnL | Trail Distance |
|----------|----------------|
| +0.15% | 0.15% |
| +0.35% | 0.20% |
| +0.50% | 0.25% |
| +1.00% | 0.30% |
| +2.00% | 0.40% |
| +3.00% | 0.50% |
| +5.00% | 0.75% |

Trail tracks from **peak** price, not current price. Catches inter-tick spikes.

---

## Risk Management

### Position Limits

| Rule | Value |
|------|-------|
| Max concurrent (futures) | 2 per exchange |
| Max concurrent (spot) | 1 |
| Max total across all | 3 |
| Max per pair | 1 (no scaling) |
| Max position size | 80% of exchange capital |
| Max total exposure | 90% of total capital |

### Daily Loss Limit

Stop all trading if daily PnL <= -(capital * 20%).

### Win-Rate Circuit Breaker

If < 40% win rate over last 20 trades, pause bot entirely.

### Rate Limit

Max 10 trades per hour (quality over quantity).

### Cooldowns

| Event | Cooldown |
|-------|----------|
| After SL hit (per pair) | 2 minutes |
| After 3 consecutive losses (per pair) | 5 minutes |
| After phantom cleared | 60 seconds |

---

## Capital Allocation

### Base Allocation Per Pair

```
XRP: 50% (best performer — maximize capital)
ETH: 30% (mixed — moderate)
BTC: 20% (lowest win rate — minimum)
```

### Performance Adjustment (last 5 trades)

- Win rate < 20%: reduce to 5% minimum
- Win rate > 60%: boost by 20% (capped at 70%)
- 2nd simultaneous position: 60% of normal allocation

### Contract Sizing (Delta Futures)

| Pair | Contract Size | Max Contracts |
|------|--------------|---------------|
| BTC/USD:USD | 0.001 BTC | 1 |
| ETH/USD:USD | 0.01 ETH | 2 |
| XRP/USD:USD | 1.0 XRP | 50 |

### Spot Sizing (Binance)

- 35% of available Binance balance
- Minimum notional: $6.01

---

## Fee Structure

### Delta India (including 18% GST)

| Type | Rate |
|------|------|
| Maker | 0.02% * 1.18 = 0.024% |
| Taker | 0.05% * 1.18 = 0.059% |
| Mixed round-trip | 0.024% + 0.059% = 0.083% |

### P&L Calculation

```
gross_pnl = (exit_price - entry_price) * coin_amount  [long]
gross_pnl = (entry_price - exit_price) * coin_amount  [short]
entry_fee = entry_notional * fee_rate
exit_fee  = exit_notional * fee_rate
net_pnl   = gross_pnl - entry_fee - exit_fee
pnl_pct   = (net_pnl / collateral) * 100
collateral = notional / leverage
```

---

## Complete Trade Lifecycle

```
SCANNING (every 5s)
  |
  +-- Fetch 30x 1m candles
  +-- Compute all 11 indicators (RSI, BB, KC, EMA, VWAP, momentum, volume)
  +-- Count bull/bear signals
  +-- Gate: 3/4 minimum? (or RSI override?)
  |
  +-- YES -> Build entry signal
  |     |
  |     +-- Risk manager approves?
  |     |     +-- Daily loss limit OK?
  |     |     +-- Position limits OK?
  |     |     +-- Balance available?
  |     |     +-- Cooldowns clear?
  |     |
  |     +-- YES -> Execute market order
  |           |
  |           +-- Fill confirmed -> Track position
  |                 |
  |                 +-- Phase 1 (0-30s): Hard SL only
  |                 +-- Phase 2 (30s-10m): Trail + breakeven + pullback + decay
  |                 +-- Phase 3 (10-30m): Flatline + timeout + safety
  |                 |
  |                 +-- EXIT triggered -> Market close order
  |                       |
  |                       +-- Record P&L (gross - fees = net)
  |                       +-- Update win/loss streak
  |                       +-- Apply cooldowns if loss
  |                       +-- Resume scanning
  |
  +-- NO -> Wait 5s, scan again
```

---

## Exit Reason Codes

| Code | Meaning | Dashboard Color |
|------|---------|----------------|
| `TRAIL` | Trailing stop triggered | Green |
| `TP` | Take profit hit | Green |
| `HARD_TP` | 10% capital gain safety | Green |
| `TP_EXCHANGE` | TP filled by exchange | Green |
| `MANUAL` | Manual close from dashboard | Blue |
| `SL` | Stop loss hit | Red |
| `SL_EXCHANGE` | SL filled by exchange | Red |
| `REVERSAL` | Signal reversal exit | Yellow |
| `PULLBACK` | Profit pullback exit | Yellow |
| `DECAY` | Momentum decay exit | Yellow |
| `FLAT` | Flatline (no movement 10m) | Gray |
| `TIMEOUT` | Hard timeout 30m | Gray |
| `BREAKEVEN` | Breakeven protection | Gray |
| `SAFETY` | Losing after timeout | Gray |
| `DUST` | Dust balance cleanup | Gray |
| `ORPHAN` | Orphaned strategy closed | Gray |
| `PHANTOM` | Phantom position cleared | Orange |
| `POSITION_GONE` | Position not found | Orange |
| `CLOSED_BY_EXCHANGE` | Closed externally | Orange |
| `EXPIRY` | Delta daily contract expiry | Gray |

---

## Key Thresholds Reference

| Parameter | Futures | Spot |
|-----------|---------|------|
| Entry gate | 3/4 signals | 3/4 signals |
| Leverage | 20x (capped) | 1x |
| SL distance | 0.40-0.50% floor | 2.0% |
| SL cap | 1.5% | 3.0% |
| Trail activation | +0.15% peak | +1.50% peak |
| Trail start distance | 0.15% | 0.80% |
| Hard TP | 10% capital | 10% capital |
| Breakeven trigger | +0.20% peak | +0.20% peak |
| Pullback exit | 30% of peak | 30% of peak |
| Decay exit | peak >= 0.30%, now < 0.10% | same |
| Flatline | 10 min, < 0.05% move | same |
| Timeout | 30 min | 30 min |
| Daily loss stop | 20% drawdown | 20% drawdown |
| Rate limit | 10 trades/hour | 10 trades/hour |
| Max concurrent | 2 per exchange | 1 |
| RSI override | < 30 or > 70 | < 30 or > 70 |
| Momentum min | 0.08% (60s) | 0.08% (60s) |
| Volume min | 0.8x average | 0.8x average |
| Mixed RT fee | 0.083% | ~0.20% |
