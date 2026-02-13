-- ============================================================================
-- Migration: Add new indicator fields to bot_status and strategy_log
-- Run in Supabase SQL Editor
-- ============================================================================

-- ── 1. BOT_STATUS: add exchange balances, connectivity, and state fields ──

alter table public.bot_status
    add column if not exists binance_balance   numeric(20,8),
    add column if not exists delta_balance     numeric(20,8),
    add column if not exists binance_connected boolean not null default false,
    add column if not exists delta_connected   boolean not null default false,
    add column if not exists bot_state         text not null default 'running'
        check (bot_state in ('running', 'paused', 'error')),
    add column if not exists shorting_enabled  boolean not null default false,
    add column if not exists leverage          integer not null default 1,
    add column if not exists active_strategy_count integer not null default 0;


-- ── 2. STRATEGY_LOG: add exchange, MACD, DI, price, and signal fields ──

alter table public.strategy_log
    add column if not exists exchange          text not null default 'binance',
    add column if not exists signal_strength   numeric(10,4),
    add column if not exists macd_value        numeric(20,8),
    add column if not exists macd_signal       numeric(20,8),
    add column if not exists macd_histogram    numeric(20,8),
    add column if not exists current_price     numeric(20,8),
    add column if not exists entry_distance_pct numeric(10,4),
    add column if not exists plus_di           numeric(10,4),
    add column if not exists minus_di          numeric(10,4);

-- Index on exchange for filtering
create index if not exists idx_strategy_log_exchange on public.strategy_log (exchange);

-- ── 3. Recreate v_bot_latest_status to include new columns ──

create or replace view public.v_bot_latest_status as
select *
from   public.bot_status
order  by timestamp desc
limit  1;

-- ── 4. View: latest strategy snapshot per pair (for dashboard gauges) ──

create or replace view public.v_strategy_latest as
select distinct on (pair)
    id, timestamp, pair, exchange,
    market_condition, strategy_selected, reason,
    adx, rsi, atr, bb_width, volume_ratio,
    signal_strength,
    macd_value, macd_signal, macd_histogram,
    current_price, entry_distance_pct,
    plus_di, minus_di
from   public.strategy_log
order  by pair, timestamp desc;
