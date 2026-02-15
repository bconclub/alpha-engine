-- ============================================================================
-- Migration: Add entry signal fields for dashboard 2-of-4 indicator display
-- Run in Supabase SQL Editor
-- ============================================================================

-- ── 1. STRATEGY_LOG: add BB bands and price change for entry signal display ──

alter table public.strategy_log
    add column if not exists bb_upper          numeric(20,8),
    add column if not exists bb_lower          numeric(20,8),
    add column if not exists price_change_15m  numeric(10,4);

-- ── 2. BOT_STATUS: add Delta INR balance and uptime ──

alter table public.bot_status
    add column if not exists delta_balance_inr numeric(20,2),
    add column if not exists uptime_seconds    integer not null default 0;

-- ── 3. DROP and recreate v_strategy_latest with new columns ──
-- (CREATE OR REPLACE cannot change column order/names)

drop view if exists public.v_strategy_latest;

create view public.v_strategy_latest as
select distinct on (pair)
    id, timestamp, pair, exchange,
    market_condition, strategy_selected, reason,
    adx, rsi, atr, bb_width, bb_upper, bb_lower,
    volume_ratio, signal_strength,
    macd_value, macd_signal, macd_histogram,
    current_price, entry_distance_pct, price_change_15m,
    plus_di, minus_di
from   public.strategy_log
order  by pair, timestamp desc;
