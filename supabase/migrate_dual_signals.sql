-- ============================================================================
-- Migration: Add per-direction signal booleans to strategy_log
-- Dashboard shows both bull AND bear dots simultaneously per pair.
-- Engine writes bull_mom/bear_mom etc. alongside the existing signal_* fields.
-- Run in Supabase SQL Editor.
-- ============================================================================

-- ── Add per-direction signal columns ──────────────────────────────────────

alter table public.strategy_log
    add column if not exists bull_mom  boolean,   -- bull: momentum signal active
    add column if not exists bull_vol  boolean,   -- bull: volume spike signal active
    add column if not exists bull_rsi  boolean,   -- bull: RSI extreme signal active
    add column if not exists bull_bb   boolean,   -- bull: BB mean-reversion signal active
    add column if not exists bear_mom  boolean,   -- bear: momentum signal active
    add column if not exists bear_vol  boolean,   -- bear: volume spike signal active
    add column if not exists bear_rsi  boolean,   -- bear: RSI extreme signal active
    add column if not exists bear_bb   boolean;   -- bear: BB mean-reversion signal active

-- ── Recreate v_strategy_latest with new columns ───────────────────────────

drop view if exists public.v_strategy_latest;

create view public.v_strategy_latest as
select distinct on (pair)
    id, timestamp, pair, exchange,
    market_condition, strategy_selected, reason,
    adx, rsi, atr, bb_width, bb_upper, bb_lower,
    volume_ratio, signal_strength,
    macd_value, macd_signal, macd_histogram,
    current_price, entry_distance_pct, price_change_15m,
    price_change_1h, price_change_24h,
    plus_di, minus_di, direction,
    signal_count, signal_side,
    signal_mom, signal_vol, signal_rsi, signal_bb,
    bull_count, bear_count,
    bull_mom, bull_vol, bull_rsi, bull_bb,
    bear_mom, bear_vol, bear_rsi, bear_bb,
    skip_reason
from   public.strategy_log
order  by pair, timestamp desc;

-- ── Also update latest_strategy_log if it exists ──────────────────────────

drop view if exists public.latest_strategy_log;

create view public.latest_strategy_log as
select distinct on (pair, exchange)
    *
from   public.strategy_log
order  by pair, exchange, created_at desc;
