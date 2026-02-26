# Alphabot — Build Truth

## What Is Alpha
Alpha is a precision momentum-trading bot built by Z at BCON Club. It trades crypto across four exchanges simultaneously — Bybit (primary USDT futures), Delta Exchange India (USD futures + options), Kraken Futures (USD perpetuals), and Binance (1x spot, long-only). The mission: grow capital through quality trades that beat the fees.

## Current Versions
- **Engine**: `3.24.6`
- **Signal Logic**: v6.3 (11-Signal Arsenal — VWAP, BB Squeeze, Liquidity Sweep, FVG, Volume Divergence + setup tracking)
- **Exchanges**: Bybit (primary), Delta India, Kraken Futures, Binance Spot

## Repository Structure
```
Alpha/
├── engine/              # Python trading engine (runs 24/7)
│   ├── alpha/
│   │   ├── main.py              # Entry point — async event loop, exchange init
│   │   ├── config.py            # Typed config from .env (dataclasses)
│   │   ├── market_analyzer.py   # 1m/15m candle analysis, signal generation
│   │   ├── trade_executor.py    # Order placement, position management
│   │   ├── price_feed.py        # WebSocket real-time price feed
│   │   ├── risk_manager.py      # Position sizing, exposure limits
│   │   ├── strategy_selector.py # Strategy rotation (currently disabled)
│   │   ├── alerts.py            # Telegram notifications
│   │   ├── db.py                # Supabase persistence
│   │   ├── utils.py             # Shared helpers
│   │   └── strategies/
│   │       ├── base.py              # BaseStrategy ABC + Signal dataclass
│   │       ├── scalp.py             # Primary strategy — v6.3 11-signal scalping
│   │       ├── options_scalp.py     # Options buying on 3/4+ signals (disabled)
│   │       ├── futures_momentum.py  # Trend-following futures (disabled)
│   │       ├── momentum.py          # General momentum (disabled)
│   │       ├── grid.py              # Grid trading (disabled)
│   │       └── arbitrage.py         # Cross-exchange arbitrage (disabled)
│   ├── SOUL.md          # Trading philosophy & rules
│   └── VERSION
├── dashboard/           # Next.js 14 real-time dashboard
│   ├── app/
│   │   ├── layout.tsx           # Root layout (sidebar offset, metadata)
│   │   ├── page.tsx             # Dashboard home
│   │   ├── trades/page.tsx      # Trade history
│   │   ├── strategies/page.tsx  # Strategy performance
│   │   ├── analytics/page.tsx   # Analytics charts
│   │   ├── settings/page.tsx    # Bot controls (pause/resume/force)
│   │   └── leaderboard/        # Pair leaderboard
│   ├── components/
│   │   ├── ui/Sidebar.tsx           # Responsive nav (hamburger + bottom bar on mobile)
│   │   ├── dashboard/
│   │   │   ├── LiveStatusBar.tsx    # Exchange balances, capital, uptime
│   │   │   ├── MarketOverview.tsx   # Signal table (RSI, ADX, BB, momentum, signals)
│   │   │   ├── TriggerProximity.tsx # How close pairs are to triggering
│   │   │   ├── LiveActivityFeed.tsx # Real-time trade/signal stream
│   │   │   ├── LivePositions.tsx    # Live position tracker (HOLDING/TRAILING state)
│   │   │   ├── PerformancePanel.tsx # P&L charts, win rate, stats
│   │   │   └── AnalyticsPanel.tsx   # Deep analytics (distribution, hourly, etc.)
│   │   ├── charts/PnLChart.tsx      # Recharts P&L visualization
│   │   ├── tables/TradeTable.tsx    # Trade history + Setup type badges
│   │   └── providers/SupabaseProvider.tsx  # Real-time Supabase context
│   ├── hooks/
│   │   └── useLivePrices.ts     # 3-second exchange API price polling
│   └── VERSION
├── supabase/            # SQL schema + migrations
│   ├── schema.sql               # Base schema (source of truth)
│   ├── fix_now.sql              # Latest migration
│   └── master.sql               # Full merged schema
├── alphabot.md          # This file — build truth
├── scripts/             # Utility scripts
└── requirements.txt     # Python dependencies
```

## Tech Stack

### Engine
- **Language**: Python 3.11+
- **Async**: asyncio event loop (runs forever)
- **Exchanges**: Bybit, Delta Exchange India, Kraken Futures, Binance (all via ccxt)
- **Real-time**: WebSocket price feeds per exchange for sub-second exit checks
  - Bybit: `wss://stream.bybit.com/v5/public/linear`
  - Delta: `wss://socket.delta.exchange`
  - Kraken: `wss://futures.kraken.com/ws/v1`
  - Binance: `wss://stream.binance.com:9443/ws`
- **Indicators**: `ta` library (Bollinger Bands, Keltner Channel, RSI, ADX, ATR, MACD)
- **Database**: Supabase (PostgreSQL) for trade logs, strategy performance, bot status
- **Alerts**: Telegram bot for trade open/close notifications with P&L
- **Config**: `.env` → typed dataclasses (BybitConfig, DeltaConfig, KrakenConfig, BinanceConfig, TradingConfig)

### Dashboard
- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS (responsive: sm/md/lg breakpoints)
- **Charts**: Recharts (LineChart, BarChart, PieChart, AreaChart)
- **Real-time**: Supabase JS client with realtime subscriptions + 3s live price polling
- **Mobile**: Fully responsive — hamburger drawer, bottom nav, card layouts on mobile

## Trading Logic

### The 11-Signal Entry Arsenal (v6.3)

Every tick, the engine evaluates 11 independent signals. Each signal fires a bull or bear tag.

| # | Signal | Tag | Detection Logic |
|---|--------|-----|----------------|
| 1 | **Momentum** | `MOM:` | 0.15%+ price move in last 60s |
| 2 | **Volume Spike** | `VOL:` | 1.2x+ average volume (pair-adjusted) |
| 3 | **RSI Extreme** | `RSI:` | RSI < 35 (long) or > 65 (short) |
| 4 | **Bollinger Band** | `BB:` | Price outside BB + mean-reversion setup |
| 5 | **5m Momentum** | `MOM5M:` | Sustained 5-candle trend confirmation |
| 6 | **Trend Continuation** | `TCONT:` | New 15-candle extreme + volume |
| 7 | **VWAP Reclaim** | `VWAP:` | Price above VWAP + 9 EMA > 21 EMA ribbon |
| 8 | **BB Squeeze** | `BBSQZ:` | BB inside Keltner Channel → breakout + volume |
| 9 | **Liquidity Sweep** | `LIQSWEEP:` | Sweep swing H/L, reclaim + RSI divergence |
| 10 | **Fair Value Gap** | `FVG:` | 3-candle imbalance gap + price filling |
| 11 | **Volume Divergence** | `VOLDIV:` | Price vs volume trend divergence (hollow moves) |

### Entry Rules
- **Gate**: ALL pairs require **3-of-4+ signals** to enter (no 2/4 coin flips)
- **RSI Override**: RSI < 30 or > 70 enters immediately regardless of signal count
- **Max**: 2 concurrent positions. 2nd only if 1st is breakeven+ AND signal is 3/4+
- **Cooldowns**: 2 min after SL, 5 min after 3 consecutive losses
- **SOL disabled**: 0% win rate, allocation set to 0%

### Setup Type Tracking
Each entry is classified by its dominant signal pattern:

| Setup Type | Trigger |
|-----------|---------|
| `VWAP_RECLAIM` | VWAP signal present |
| `BB_SQUEEZE` | BB Squeeze breakout signal |
| `LIQ_SWEEP` | Liquidity sweep signal |
| `FVG_FILL` | Fair Value Gap fill signal |
| `VOL_DIVERGENCE` | Volume divergence signal |
| `TREND_CONT` | Trend continuation signal |
| `MOMENTUM_BURST` | MOM + VOL combo |
| `MEAN_REVERT` | BB + RSI combo |
| `RSI_OVERRIDE` | RSI extreme override entry |
| `MULTI_SIGNAL` | 4+ different signals fired |
| `MIXED` | Fallback — no dominant pattern |

Setup type is stored in the `setup_type` column of the trades table and displayed as color-coded badges on the dashboard.

### 3-Phase Exit System

| Phase | Time | Behavior |
|-------|------|----------|
| **Phase 1** | 0-30s | HANDS OFF — only hard SL fires. Skip to Phase 2 if peak PnL >= +0.5% |
| **Phase 2** | 30s-10min | WATCH — move SL to entry at +0.20%, activate trail at +0.15%, profit pullback, signal reversal |
| **Phase 3** | 10-30min | CLEANUP — flatline exit (< 0.05% move in 10 min), hard timeout at 30 min |

### Dynamic Trailing Tiers
Trail activates at +0.15% (any green = trail it). Distance widens with profit, never tightens.

| Peak PnL | Trail Distance | Behavior |
|----------|---------------|----------|
| +0.15% | 0.15% | Instant trail — any green locks profit |
| +0.35% | 0.20% | Tight scalp lock |
| +0.50% | 0.25% | Standard trail |
| +1.00% | 0.30% | Moderate trail |
| +2.00% | 0.40% | Tighter lock |
| +3.00% | 0.50% | Lock hard |
| +5.00% | 0.75% | Still tight for scalping |

### Exit Reasons
- **SL**: Hard stop loss hit (all phases)
- **TRAIL**: Trailing stop hit (phase 2+)
- **BREAKEVEN**: Price returned to entry after peak >= +0.20%
- **PROFIT_PULLBACK**: Lost 30%+ of peak profit (peak >= +0.50%)
- **PROFIT_DECAY**: Profit faded below +0.10% after peaking >= +0.30%
- **SIGNAL_REVERSAL**: RSI/momentum flipped against position (phase 2+, pnl >= +0.30%)
- **FLATLINE**: Momentum dead < 0.05% move in 10 min (phase 3)
- **TIME_LIMIT**: 30 min max hold timeout

### Per-Pair Stop Loss
| Pair | SL Floor | TP Floor |
|------|----------|----------|
| BTC | 0.40% | 1.50% |
| ETH | 0.45% | 1.50% |
| XRP | 0.50% | 2.00% |
| Default | 0.35% | 1.50% |

SL/TP also adapt dynamically via ATR (SL = 1.5x ATR, TP = 4x ATR), using whichever is wider.

### Indicators Computed Every Tick
- **RSI** (14-period) — entry signals + exit reversal
- **Bollinger Bands** (20, 2σ) — squeeze, mean-reversion, breakout
- **Keltner Channel** (20, 1.5x ATR) — BB Squeeze detection
- **ADX** (14-period) — trend strength context
- **ATR** (14-period) — dynamic SL/TP sizing
- **MACD** (12, 26, 9) — momentum context
- **VWAP** (30-candle session) — price-value alignment
- **9/21 EMA Ribbon** — trend direction for VWAP signal
- **Volume Ratio** — current vs 20-period average

### Fee Structure (Per Exchange)

| Exchange | Taker/side | Maker/side | Mixed RT | Notes |
|----------|-----------|-----------|----------|-------|
| **Bybit** | 0.055% | 0.02% | 0.075% | No GST — global |
| **Delta India** | 0.059% | 0.024% | 0.083% | +18% GST |
| **Kraken Futures** | 0.05% | 0.02% | 0.07% | No GST — global |
| **Binance Spot** | 0.10% | 0.10% | 0.20% | Flat |

Strategy: Limit entry (maker) + Market exit (taker) = mixed round-trip.

## Live Position State
The engine writes position state to the DB every ~10 seconds while a position is open:
- **`position_state`**: `'holding'` or `'trailing'` — displayed on dashboard as HOLDING/TRAILING badge
- **`trail_stop_price`**: Current trailing stop price (only when trailing)
- **`current_pnl`**: Unrealized P&L % (price move)
- **`current_price`**: Latest price seen by bot
- **`peak_pnl`**: Highest P&L % reached during the trade

Dashboard reads via `v_open_positions` view with 3-second live price polling overlay.

## Active Strategies
- **scalp** — Primary. v6.3 11-signal momentum scalping on Bybit + Delta + Kraken futures + Binance spot

Inactive/disabled: `options_scalp`, `futures_momentum`, `momentum`, `grid`, `arbitrage`, `strategy_selector`

## Key Protections
- **Orphan protection**: Per-exchange reconciliation every 60s (Bybit, Delta, Kraken)
- **Phantom protection**: Time guards + cooldown + rate-limited logs
- **Telegram resilience**: Auto-reconnect on failure + health check + clean shutdown
- **WebSocket price feed**: Per-exchange WS for sub-second SL/trail execution
- **Cooldowns**: 2 min after SL, 5 min after 3 consecutive losses
- **Phase 1 hands-off**: 30s grace period prevents fill-bounce exits
- **Multi-exchange balance tracking**: Independent capital per exchange, aggregate view on dashboard

## Infrastructure
- **Engine runs on**: VPS (24/7 process)
- **Dashboard hosted on**: Vercel (Next.js)
- **Database**: Supabase (PostgreSQL + Realtime)
- **Alerts**: Telegram Bot API

## Soul Document
The trading philosophy and complete rule set lives at `engine/SOUL.md`. It defines Alpha's identity, entry/exit logic, fee awareness, and personality. Every code change must respect the Soul.

## Build Commands
```bash
# Engine
cd engine && pip install -r ../requirements.txt
python -m alpha.main

# Dashboard
cd dashboard && npm install
npm run dev      # Development
npm run build    # Production build
```

## Commit History
Major milestones:
- v5.2: Focused Signal — pure 2-of-4, max 2 positions, cooldowns
- v5.3: ATR-dynamic SL/TP, performance-based allocation, strength gates
- v5.4: Fast exit — 1s ticks, aggressive trail, profit decay protection
- v5.5: Smart trend — soft 15m weight, per-pair streaks, multi-TF momentum
- v5.6: WebSocket price feed for real-time exit checks
- v5.7: Orphan + phantom protection
- v5.8: Trail tier tightening, aggressive profit lock
- v5.9: Entry quality gate (3/4 required, RSI override)
- v6.0: Dashboard live prices, engine version display
- v6.1: Emergency signal fix (OR→AND gate enforcement)
- v6.2: VWAP Reclaim signal + setup type tracking per trade
- v6.3: 11-signal arsenal (BB Squeeze, Liquidity Sweep, FVG, Volume Divergence)
- GPFC #20: Bybit exchange integration (primary futures)
- GPFC #21: Options exit system port + Delta orphan fix
- GPFC #22: Kraken Futures exchange integration (4th exchange)

---
*Born February 14, 2026. Built by Z @ BCON Club.*
