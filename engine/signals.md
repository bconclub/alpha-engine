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

### Directional Signal Counting

Each signal is counted separately for bull and bear directions. The 3/4 gate applies to the **directional** count, not a combined total.

**Dashboard shows both sides:**
- `Bull 3/4` (green bar) — 3 signals aligned bullish
- `Bear 1/4` (red bar) — 1 signal aligned bearish
- The active side (the one that would trigger entry) is **bold + colored**

**Per-signal direction rules (Core 4):**

| Signal | Bull Condition | Bear Condition |
|--------|---------------|----------------|
| MOM | momentum_60s >= +0.08% | momentum_60s <= -0.08% |
| VOL | vol_ratio >= 0.8x AND momentum > 0 | vol_ratio >= 0.8x AND momentum < 0 |
| RSI | RSI(14) < 35 | RSI(14) > 65 |
| BB | price_position <= 0.15 (bottom 15%) | price_position >= 0.85 (top 15%) |

The dashboard indicator dots (MOM, VOL, RSI, BB) show only the **active side's** signals. A SHORT display will never show RSI=42 as active because RSI at 42 does not meet the bear condition (>65).

**Data flow:** `scalp.py` → `last_signal_state` (with `bull_count`, `bear_count`, directional booleans) → `main.py` → `strategy_log` DB → Dashboard `TriggerProximity.tsx`

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

### Always Active (ALL phases)

| Exit Type | Trigger | Color |
|-----------|---------|-------|
| **Hard SL** | Price hits SL floor (0.25% all pairs) | Red |
| **Ratchet Floor** | Capital PnL drops below locked floor → instant exit | Green |
| **Hard TP** | Capital PnL >= 10% AND trail NOT active (safety net) | Green |

### Ratcheting Profit Floors (cannot go back down)

| Capital PnL Reached | Floor Locks At |
|---------------------|----------------|
| +3% | 0% (breakeven) |
| +5% | +2% |
| +8% | +5% |
| +10% | +7% |
| +15% | +10% |

If current capital PnL drops below the locked floor → **EXIT IMMEDIATELY** (market order, reason: `PROFIT_LOCK`).

### Phase 1: Hands-Off (0-30 seconds)

Only hard SL, ratchet floors, and Hard TP fire. Protects against fill slippage.

Exception: If peak PnL >= +0.5%, skip immediately to Phase 2.

**Stop Loss Distances (per-pair floor):**

| Pair | SL Floor | Cap |
|------|----------|-----|
| BTC | 0.25% | 0.50% (futures) |
| ETH | 0.25% | 0.50% (futures) |
| XRP | 0.25% | 0.50% (futures) |
| Spot | 2.0% | 3.0% (spot) |

Dynamic SL: `max(pair_floor, ATR_14 * 1.5)`

### Phase 2: Watch & Trail (30s - 10 minutes)

| Exit Type | Trigger | Color |
|-----------|---------|-------|
| **Trailing Stop** | Peak PnL >= +0.25% activates trail | Green |
| **Breakeven** | Peak >= +0.20% AND price returns to entry | Gray |
| **Decay Emergency** | Peak capital >= +3% AND current < peak × 40% (lost 60%+) | Yellow |
| **Signal Reversal** | In profit >= +0.30% AND RSI flips or momentum reverses | Yellow |

### Phase 3: Trail or Cut (10 - 30 minutes)

| Exit Type | Trigger | Color |
|-----------|---------|-------|
| **Trailing** | Continues from Phase 2 | Green |
| **Decay Emergency** | Same as Phase 2 (repeated check) | Yellow |
| **Flatline** | 10+ min hold AND \|PnL\| < 0.05% — **ONLY if losing** | Gray |
| **Timeout** | 30 min hold AND not trailing — **ONLY if losing** | Gray |
| **Safety** | 30 min hold AND PnL < 0% (cut losers) | Gray |

**Winners are NEVER closed by flatline or timeout.** Only trail, ratchet, or decay emergency can close a winner.

### Fee-Aware Minimum

Non-forced exits (TRAIL, BREAKEVEN, DECAY_EMERGENCY, REVERSAL, FLAT, TIMEOUT) are skipped if gross profit < $0.10. Exception: SL, PROFIT_LOCK, HARD_TP, and SAFETY always execute regardless of gross amount.

---

## Trailing Stop Tiers

Trail activates at +0.25% peak PnL (+5% capital at 20x). Distance widens as profit grows (never tightens):

| Peak PnL | Trail Distance | Locked Min | Capital at 20x |
|----------|----------------|------------|----------------|
| +0.25% | 0.10% | +0.15% | +3% |
| +0.50% | 0.15% | +0.35% | +7% |
| +1.00% | 0.20% | +0.80% | +16% |
| +2.00% | 0.30% | +1.70% | +34% |

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

### Exit Fee Optimization

Non-urgent exits (TRAIL, TP, PULLBACK, DECAY, REVERSAL, FLAT, TIMEOUT, BREAKEVEN, SAFETY) use **limit-then-market** strategy:
1. Place limit order at current price (maker fee: 0.024%)
2. Wait 3 seconds for fill
3. If not filled → cancel limit, execute market order (taker fee: 0.059%)

Urgent exits (SL, HARD_TP, SL_EXCHANGE) always use immediate market orders — speed > fees.

Savings: ~$0.02-0.03 per exit when limit fills (~60% fee reduction on exit leg).

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
  |                 +-- Phase 1 (0-30s): SL + ratchet + hard TP only
  |                 +-- Phase 2 (30s-10m): Trail + breakeven + decay emergency
  |                 +-- Phase 3 (10-30m): Trail + flat/timeout (losers only)
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
| `PROFIT_LOCK` | Ratchet floor breached (locked profit) | Green |
| `TP` | Take profit hit | Green |
| `HARD_TP` | 10% capital gain safety (if not trailing) | Green |
| `TP_EXCHANGE` | TP filled by exchange | Green |
| `MANUAL` | Manual close from dashboard | Blue |
| `SL` | Stop loss hit | Red |
| `SL_EXCHANGE` | SL filled by exchange | Red |
| `DECAY_EMERGENCY` | Lost 60%+ of peak profit | Yellow |
| `REVERSAL` | Signal reversal exit | Yellow |
| `FLAT` | Flatline (no movement 10m, losers only) | Gray |
| `TIMEOUT` | Hard timeout 30m (losers only) | Gray |
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
| SL distance | 0.25% floor | 2.0% |
| SL cap | 0.50% | 3.0% |
| Trail activation | +0.25% peak | +1.50% peak |
| Trail start distance | 0.10% | 0.80% |
| Hard TP | 10% capital (if not trailing) | 10% capital |
| Ratchet floors | +3%→0%, +5%→2%, +8%→5%, +10%→7%, +15%→10% | N/A |
| Breakeven trigger | +0.20% peak | +0.20% peak |
| Decay emergency | peak >= 3% cap, current < peak × 40% | same |
| Fee minimum | gross > $0.10 (except SL/ratchet) | same |
| Flatline | 10 min, losers only | same |
| Timeout | 30 min, losers only | same |
| Daily loss stop | 20% drawdown | 20% drawdown |
| Rate limit | 10 trades/hour | 10 trades/hour |
| Max concurrent | 2 per exchange | 1 |
| RSI override | < 30 or > 70 | < 30 or > 70 |
| Momentum min | 0.08% (60s) | 0.08% (60s) |
| Volume min | 0.8x average | 0.8x average |
| Mixed RT fee | 0.083% | ~0.20% |
