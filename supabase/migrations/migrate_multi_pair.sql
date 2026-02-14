-- ============================================================================
-- ALPHA BOT — Multi-pair migration
-- Run this in your Supabase SQL Editor if the tables already exist.
-- Adds the `pair` column to strategy_log for multi-pair tracking.
-- ============================================================================

-- Add pair column to strategy_log (safe if already exists)
alter table public.strategy_log
    add column if not exists pair text not null default 'BTC/USDT';

-- Index for pair-based queries
create index if not exists idx_strategy_log_pair
    on public.strategy_log (pair);

-- Drop the old view first (column order changed — CREATE OR REPLACE can't reorder)
drop view if exists public.v_strategy_distribution_24h;

-- Recreate with pair as first column
create view public.v_strategy_distribution_24h as
select
    pair,
    strategy_selected,
    market_condition,
    count(*)                    as occurrences,
    min(timestamp)              as first_seen,
    max(timestamp)              as last_seen
from   public.strategy_log
where  timestamp >= now() - interval '24 hours'
group  by pair, strategy_selected, market_condition
order  by occurrences desc;
