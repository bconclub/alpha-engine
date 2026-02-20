**Last updated: v3.13.6 — 2026-02-20**

# Alpha Trade Signals — Complete Reference

How the bot decides when to enter, how it manages positions, and when it exits.

---

## 11-Signal Arsenal

Every 5 seconds the bot computes all 11 indicators from 30 x 1m candles. Each signal fires independently as bullish or bearish. Entry requires **3/4 signals minimum** aligned in the same direction as momentum.

### Core 4 Signals

| # | Signal | Tag | Long Condition | Short Condition |
|---|--------|-----|---------------|-----------------|
| 1 | **Momentum 60s** | MOM | momentum_60s >= +0.15% | momentum_60s <= -0.15% |
| 2 | **Volume Spike** | VOL | vol_ratio >= 0.8x AND mom > 0 | vol_ratio >= 0.8x AND mom < 0 |
| 3 | **RSI Extremes** | RSI | RSI(14) < 35 | RSI(14) > 65 |
| 4 | **Bollinger Band** | BB | bb_position <= 0.15 (bottom 15%) | bb_position >= 0.85 (top 15%) |

### Bonus 7 Signals

| # | Signal | Tag | Condition |
|---|--------|-----|-----------|
| 5 | **Momentum 5m** | MOM5m | \|momentum_300s\| >= 0.30% — catches slow sustained moves |
| 6 | **Trend Continuation** | TCONT | New 15-candle high/low + volume >= 1.0x average |
| 7 | **VWAP + EMA Alignment** | VWAP | Long: price > VWAP(30) AND EMA(9) > EMA(21). Short: inverse |
| 8 | **BB Squeeze Breakout** | BBSQZ | BB inside Keltner Channel (squeeze) + price breaks out with volume |
| 9 | ~~**Liquidity Sweep**~~ | ~~LIQSWEEP~~ | **DISABLED** — poor performance (134-contract sweep lost $0.82) |
| 10 | **Fair Value Gap** | FVG | 3-candle imbalance gap >= 0.05%, price fills the gap |
| 11 | **Volume Divergence** | VOLDIV | Price rising + volume declining 20% (hollow pump), or price falling + volume declining 20% (exhausted sellers) |

---

## Entry Gate

### Gate 0 — Momentum Direction Lock

Momentum must be above the **0.15% minimum** (Gate 0). Direction is locked:
- `momentum_60s > 0` → `mom_direction = "long"` — only long entries allowed
- `momentum_60s < 0` → `mom_direction = "short"` — only short entries allowed

If signals fire against the momentum direction, the entry is **blocked** and logged:
```
[BTC/USD:USD] DIRECTION BLOCK: 3 bear signals but mom is +0.150% (long)
```

### Momentum Strength Tiers

After Gate 0, momentum is scored:

| Tier | Momentum | Required Signals | Meaning |
|------|----------|-----------------|---------|
| **STRONG** | >= 0.20% | 3/4 | High conviction, standard entry |
| **MODERATE** | 0.15% - 0.20% | 3/4 | Just above gate, standard entry |
| Below gate | < 0.15% | — | Blocked by Gate 0, no entry possible |

> **Note:** The old WEAK tier (0.08-0.12%) no longer exists — the gate at 0.15% eliminates weak-momentum entries entirely.

### RSI Override

RSI < 30 or RSI > 70 triggers **immediate entry** bypassing the 3/4 signal count, but still requires momentum in the matching direction (and momentum >= 0.15%).

### Big Size Signal Gate (v3.13.6)

Positions exceeding **50 contracts** require **4/4 signals** — no exceptions. This prevents large capital commitment on marginal setups.

```
BIG_SIZE_GATE: XRP/USD:USD 62 contracts > 50 — needs 4/4 signals but got 3/4, skipping
```

### Large Position Momentum Gate (v3.13.4)

Large positions (per-pair thresholds below) require **0.12%+ momentum**:

| Pair | Threshold |
|------|-----------|
| XRP | >= 50 contracts |
| ETH | >= 3 contracts |
| BTC | >= 2 contracts |
| SOL | >= 3 contracts |

### Entry Requirements Summary

```
Standard:    3/4 signals + momentum >= 0.15% + direction match
RSI <30:     immediate long entry (still needs mom_direction == "long" + mom >= 0.15%)
RSI >70:     immediate short entry (still needs mom_direction == "short" + mom >= 0.15%)
>50 contracts: 4/4 signals required (BIG_SIZE_GATE)
Large pos:   momentum >= 0.12% required (LARGE_POS_GATE)
Counter-trend: 4/4 signals (longing in TRENDING_DOWN or shorting in TRENDING_UP)
High vol:    4/4 signals required
2nd pos:     3/4+ signal strength, 60% reduced allocation
Post-streak: 3/4 signal strength (first trade after 3 consecutive losses)
```

### Disabled Setups

These setup types are **hardcoded off** — the engine will never enter on them:
- `TREND_CONT` — trend continuation
- `BB_SQUEEZE` — Bollinger squeeze breakout
- `LIQ_SWEEP` — liquidity sweep (**also disabled in code**: `if False`)

If a signal combination maps to a disabled setup, entry is blocked and logged:
```
[ETH/USD:USD] SETUP_BLOCKED: TREND_CONT — hardcoded disable
```

### Idle Threshold Widening

After 30 minutes with no entry, thresholds loosen by 20%:
- Momentum: 0.15% → 0.12%
- Volume: 0.8x → 0.64x
- RSI thresholds widen proportionally

---

## Setup Classification

The combination of signals that fire determines the setup type:

| Setup Type | Trigger |
|------------|---------|
| `RSI_OVERRIDE` | RSI < 30 or RSI > 70 (immediate entry) |
| `MOMENTUM_BURST` | MOM + VOL signals fire together |
| `MEAN_REVERT` | BB + RSI signals (band bounce) |
| `VWAP_RECLAIM` | VWAP signal active |
| ~~`TREND_CONT`~~ | ~~TCONT signal~~ — **DISABLED** |
| ~~`BB_SQUEEZE`~~ | ~~BBSQZ signal~~ — **DISABLED** |
| ~~`LIQ_SWEEP`~~ | ~~LIQSWEEP signal~~ — **DISABLED** |
| `FVG_FILL` | FVG signal (fair value gap) |
| `VOL_DIVERGENCE` | VOLDIV signal (volume divergence) |
| `MULTI_SIGNAL` | 4+ signals fired simultaneously |
| `MIXED` | Default fallback |

---

## Momentum Riding Exit System (v3.13.5)

Replaces the old tight trailing stop with a two-mode system: **ride momentum** with a wide ratchet floor, or **exit immediately** on signal reversal.

### Always Active (ALL phases)

| Exit Type | Trigger |
|-----------|---------|
| **Hard SL** | Price hits SL floor (0.25% all pairs, capped at 0.50% futures / 3.0% spot) |
| **Hard TP** | Capital PnL >= 10% (safety net) |
| **Ratchet Floor** | PnL drops below locked floor → instant exit (`RATCHET`) |

### Ratchet Floor Table

Floors are based on **price PnL %** (not capital %). Floor only moves UP — once locked, it never decreases.

| Peak PnL % Reached | Floor Locks At | Capital at 20x |
|---------------------|----------------|----------------|
| +0.30% | +0.10% | +2.0% |
| +0.50% | +0.20% | +4.0% |
| +1.00% | +0.50% | +10.0% |
| +2.00% | +1.00% | +20.0% |
| +3.00% | +1.75% | +35.0% |
| +5.00% | +3.00% | +60.0% |

If current PnL drops below the locked floor → **FLOOR_EXIT** (market order, reason: `RATCHET`).

### Phase 1: Hands-Off (0-30 seconds)

Only hard SL, ratchet floors, and Hard TP fire. Protects against fill slippage.

Exception: If peak PnL >= +0.5%, skip immediately to Phase 2.

**Stop Loss Distances:**

| Pair | SL Floor | SL Cap |
|------|----------|--------|
| BTC | 0.25% | 0.50% (futures) |
| ETH | 0.25% | 0.50% (futures) |
| XRP | 0.25% | 0.50% (futures) |
| SOL | 0.25% | 0.50% (futures) |
| Spot | 2.0% | 3.0% |

Dynamic SL: `max(pair_floor, ATR_14 * 1.5)`, capped at the SL Cap.

### Phase 2: Momentum Riding (30s - 10 minutes)

**MODE 1 — RIDING (momentum aligned + profitable):**
- Momentum_60s is in the same direction as the position → **STAY IN**
- Ratchet floor protects profits passively
- Breakeven safety: if peak was >= +0.30% and price returns to entry → exit `BREAKEVEN`
- Log: `RIDING: XRP peak=+0.45% floor=+0.10% mom=0.180% — momentum aligned`

**MODE 2 — SIGNAL REVERSAL (exit immediately):**

Three reversal triggers (any one fires → exit at market):

| Trigger | Condition | Example |
|---------|-----------|---------|
| **Momentum Flip** | Long + momentum_60s < 0, or Short + momentum_60s > 0 | `mom_flip (-0.120%)` |
| **Momentum Dying** | abs(momentum_60s) < 0.04% | `mom_dying (0.025% < 0.04%)` |
| **RSI Extreme Cross** | Long + RSI crosses above 70, or Short + RSI crosses below 30 | `RSI_cross_72>70` |

Reversal exit requires **minimum +0.10% profit** (don't exit a loser on reversal — SL handles losers).

If reversal fires but NOT in profit, and peak was >= +0.30% → **BREAKEVEN** exit instead.

### Phase 2: Spot Profit Protection (unchanged)

| Exit Type | Trigger |
|-----------|---------|
| **Pullback** | Peak >= +0.50% AND current < peak × 50% |
| **Decay** | Peak >= +0.40% AND current < +0.15% |
| **Breakeven** | Peak >= +0.30% AND current <= +0.05% |

### Phase 3: Trail or Cut (10 - 30 minutes)

| Exit Type | Trigger |
|-----------|---------|
| **Flatline** | 10+ min hold AND \|PnL\| < 0.05% — **ONLY if losing** |
| **Timeout** | 30 min hold AND not trailing — **ONLY if losing** |
| **Safety** | 30 min hold AND PnL < 0% (cut losers) |

**Winners are NEVER closed by flatline or timeout.** Only ratchet floor, reversal, or hard TP can close a winner.

### Price-Only Exits (WebSocket tick — `check_exits_immediate`)

Every price tick also checks (no OHLCV/momentum needed):
- Hard SL
- Hard TP (10% capital)
- Ratchet floor (updates peak + checks floor)
- Breakeven (if peaked high enough)
- Phase 3 flatline/timeout

This catches fast moves between 5-second OHLCV cycles.

### Fee-Aware Minimum

Discretionary exits are skipped if gross profit < $0.10. **Protective exits always execute regardless of gross amount:**

| Always Execute | Fee Check Applies |
|----------------|-------------------|
| SL, TRAIL, BREAKEVEN, PROFIT_LOCK | REVERSAL |
| HARD_TP, HARD_TP_10PCT, RATCHET, SAFETY | FLAT, TIMEOUT |

---

## Risk Management

### Position Limits

| Rule | Value |
|------|-------|
| Max concurrent (futures) | Unlimited (allocation % handles sizing) |
| Max concurrent (spot) | 1 |
| Max per pair | 1 (no scaling) |
| 2nd position strength | 3/4+ signals |

### Daily Loss Limit

Stop all trading if daily PnL <= -(capital x 20%).

### Rate Limit

Max 10 trades per hour.

### Cooldowns (Per-Pair)

| Event | Cooldown |
|-------|----------|
| After SL hit | 2 minutes |
| After 3 consecutive losses | 5 minutes |
| First trade after streak pause | requires 3/4 signals |
| After phantom cleared | 60 seconds |

---

## Capital Allocation (v3.13.6)

### Dynamic Position Sizing — No Hardcoded Contract Caps

Sizing flow:
1. Get allocation % for pair (performance-based, adaptive)
2. Reduce for 2nd simultaneous position (60% factor)
3. Calculate collateral from allocation
4. Safety cap: never use more than 80% of balance on one position
5. Total exposure cap: never exceed 90% of balance across all positions
6. Calculate notional at leverage, convert to contracts (round down)
7. Minimum 1 contract — if can't afford, skip (`INSUFFICIENT_CAPITAL`)
8. Large position gate: per-pair contract thresholds require 0.12% momentum
9. Big size gate: >50 contracts requires 4/4 signals

### Per-Pair Base Allocation (% of exchange capital)

| Pair | Allocation | Rationale |
|------|------------|-----------|
| XRP | **30%** | **CAPPED from 50%** — SLs at 100+ contracts too costly (~$0.50+) |
| ETH | 30% | Mixed, catches big moves |
| BTC | 20% | Lowest win rate, diversification |
| SOL | 15% | Newer, building data |

### Performance Adjustment (last 5 trades per pair)

- Win rate < 20%: reduce to 25% of base (minimum 5%)
- Win rate > 60%: boost by 20% (capped at 70%)

### Balance Refresh

Exchange balance is refreshed from API every 60 seconds before sizing decisions, so new deposits are picked up automatically.

### Spot Sizing (Binance)

- 50% of available Binance USDT balance
- Minimum notional: $6.00

---

## Fee Structure

### Delta India (including 18% GST)

| Type | Rate |
|------|------|
| Maker | 0.02% x 1.18 = 0.024% |
| Taker | 0.05% x 1.18 = 0.059% |
| Mixed round-trip | 0.024% + 0.059% = 0.083% |

### Exit Fee Optimization

Non-urgent exits use **limit-then-market** strategy:
1. Place limit order at current price (maker fee: 0.024%)
2. Wait 3 seconds for fill
3. If not filled → cancel limit, execute market order (taker fee: 0.059%)

Urgent exits (SL, HARD_TP, SL_EXCHANGE) always use immediate market orders.

---

## Complete Trade Lifecycle

```
SCANNING (every 5s)
  │
  ├── Fetch 30x 1m candles
  ├── Compute all 11 indicators (RSI, BB, KC, EMA, VWAP, momentum, volume)
  ├── Gate 0: momentum >= 0.15%? → sets direction (long/short)
  ├── Momentum tier: STRONG/MODERATE → sets required signals (3/4)
  ├── Count bull/bear signals (each signal fires for one direction)
  ├── Setup blocked? (TREND_CONT, BB_SQUEEZE, LIQ_SWEEP disabled)
  ├── Gate: signal_count >= required? OR RSI override?
  │
  ├── YES → Build entry signal
  │     │
  │     ├── Direction matches momentum? (bull signals + long mom, or bear + short mom)
  │     │     ├── NO → DIRECTION BLOCK (logged, no entry)
  │     │     └── YES ↓
  │     │
  │     ├── Per-pair strength gate (3/4 for all pairs)
  │     ├── Dynamic position sizing (allocation → collateral → contracts)
  │     ├── Big size gate: >50 contracts → 4/4 required?
  │     ├── Large pos gate: per-pair threshold + 0.12% momentum?
  │     │
  │     ├── Risk manager approves?
  │     │     ├── Daily loss limit OK?
  │     │     ├── Balance available?
  │     │     └── Cooldowns clear?
  │     │
  │     └── YES → Execute order
  │           │
  │           └── Fill confirmed → Track position
  │                 │
  │                 ├── Phase 1 (0-30s): SL + ratchet floor + hard TP only
  │                 ├── Phase 2 (30s-10m): Momentum riding / signal reversal
  │                 │     ├── MODE 1: momentum aligned → RIDE (stay in)
  │                 │     └── MODE 2: momentum flip/dying/RSI → REVERSAL EXIT
  │                 └── Phase 3 (10-30m): Flatline/timeout (losers only)
  │                       │
  │                       └── EXIT triggered
  │                             ├── Protective exits (SL/RATCHET/BREAKEVEN/etc): always execute
  │                             ├── Discretionary exits: skip if gross < $0.10
  │                             ├── Record P&L (gross - fees = net)
  │                             ├── Update win/loss streak
  │                             ├── Apply cooldowns if loss
  │                             └── Resume scanning
  │
  └── NO → Wait 5s, scan again
```

---

## Exit Reason Codes

| Code | Meaning |
|------|---------|
| `SL` | Stop loss hit |
| `SL_EXCHANGE` | SL filled by exchange |
| `RATCHET` | Ratchet floor breached (locked profit dropped below floor) |
| `REVERSAL` | Signal reversal exit (momentum flip, dying, or RSI extreme cross) |
| `HARD_TP_10PCT` | 10% capital gain safety net |
| `TP` | Take profit hit |
| `TP_EXCHANGE` | TP filled by exchange |
| `BREAKEVEN` | Breakeven protection (peaked high, returned to entry) |
| `FLAT` | Flatline (no movement 10m, losers only) |
| `TIMEOUT` | Hard timeout 30m (losers only) |
| `SAFETY` | Losing after timeout |
| `SPOT_PULLBACK` | Spot: profit pulled back > 50% from peak |
| `SPOT_DECAY` | Spot: profit decayed below 0.15% after peaking |
| `SPOT_BREAKEVEN` | Spot: profit decayed to near-zero after peaking |
| `MANUAL` | Manual close from dashboard |
| `DUST` | Dust balance cleanup |
| `ORPHAN` | Orphaned strategy closed |
| `PHANTOM` | Phantom position cleared |
| `POSITION_GONE` | Position not found |
| `CLOSED_BY_EXCHANGE` | Closed externally |
| `EXPIRY` | Delta daily contract expiry |

---

## Skip Reason Codes (v3.13.2)

When the bot decides NOT to enter, the reason is tracked and shown on the dashboard:

| Code | Meaning |
|------|---------|
| `NO_MOMENTUM` | abs(momentum_60s) < 0.15% — no real movement |
| `STRENGTH_GATE` | Signal count below minimum (e.g., 2/4 < 3/4) |
| `DIRECTION_BLOCK` | Signals fire against momentum direction |
| `INSUFFICIENT_CAPITAL` | Can't afford 1 contract with current allocation |
| `LARGE_POS_GATE` | Large position needs 0.12%+ momentum |
| `BIG_SIZE_GATE` | >50 contracts needs 4/4 signals |
| `SETUP_BLOCKED` | Setup type is disabled (TREND_CONT, BB_SQUEEZE, LIQ_SWEEP) |
| `POSITION_SIZE_ZERO` | Sizing returned zero |
| `WEAK_COUNTER_TREND` | Weak momentum against 15m trend |

---

## Key Thresholds Reference

| Parameter | Futures | Spot |
|-----------|---------|------|
| Entry gate | 3/4 signals (4/4 if >50 contracts) | 3/4 |
| Momentum minimum | **0.15%** (60s) | same |
| Momentum direction | must match signal direction | same |
| Leverage | 20x (capped) | 1x |
| SL distance | 0.25% floor, 0.50% cap | 2.0% floor, 3.0% cap |
| Exit system | **Momentum riding + ratchet floor** | Spot trail + pullback protection |
| Hard TP | 10% capital | same |
| Ratchet floors | +0.30%→0.10%, +0.50%→0.20%, +1.00%→0.50%, +2.00%→1.00%, +3.00%→1.75%, +5.00%→3.00% | N/A |
| Reversal exit | momentum flip / dying < 0.04% / RSI cross 70/30 | N/A |
| Reversal min profit | +0.10% PnL | N/A |
| Breakeven trigger | +0.30% peak | +0.30% peak |
| Fee minimum | gross > $0.10 (discretionary exits only) | same |
| Flatline | 10 min, losers only | same |
| Timeout | 30 min, losers only | same |
| Daily loss stop | 20% drawdown | same |
| Rate limit | 10 trades/hour | same |
| Max concurrent | Unlimited (sizing caps exposure) | 1 |
| XRP allocation | **30%** (capped from 50%) | N/A |
| ETH allocation | 30% | N/A |
| BTC allocation | 20% | N/A |
| SOL allocation | 15% | N/A |
| Max collateral | 80% of balance per position | N/A |
| Max total exposure | 90% of balance | N/A |
| 2nd position | 60% of normal allocation | N/A |
| Big size gate | >50 contracts → 4/4 signals | N/A |
| Large pos gate | Per-pair threshold → 0.12% momentum | N/A |
| RSI override | < 30 or > 70 | same |
| Volume min | 0.8x average | same |
| Disabled setups | TREND_CONT, BB_SQUEEZE, LIQ_SWEEP | same |
| Mixed RT fee | 0.083% | ~0.20% |
