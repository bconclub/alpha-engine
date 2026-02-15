-- Migration: Add direction column to strategy_log
-- Run this in Supabase SQL Editor
-- The direction field stores the 15m trend direction: 'bullish', 'bearish', or 'neutral'
-- This is used by the dashboard to show whether the trend filter blocks entry signals.

-- ── 1. Add direction column ──────────────────────────────────────────────────
alter table public.strategy_log
    add column if not exists direction text default 'neutral';

-- ── 2. Backfill from plus_di / minus_di / adx (matches engine logic) ─────────
update public.strategy_log
set direction = case
    when adx > 25 and abs(plus_di - minus_di) > 5 then
        case when plus_di > minus_di then 'bullish' else 'bearish' end
    when adx >= 20 then
        case when plus_di > minus_di then 'bullish' else 'bearish' end
    else 'neutral'
end
where direction is null or direction = 'neutral';

-- ── 3. Update the latest_strategy_log view to include direction ──────────────
create or replace view public.latest_strategy_log as
select distinct on (pair, exchange)
       *
from   public.strategy_log
order  by pair, exchange, timestamp desc;
