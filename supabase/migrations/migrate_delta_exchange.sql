-- ============================================================================
-- ALPHA BOT — Delta Exchange migration
-- Run this in your Supabase SQL Editor.
-- Adds futures support: leverage, position_type columns on trades;
-- updates strategy check constraints for futures_momentum.
-- ============================================================================

-- 1. Add leverage column to trades (1 = spot, >1 = futures)
alter table public.trades
    add column if not exists leverage numeric(5,2) not null default 1;

-- 2. Add position_type column (spot / long / short)
alter table public.trades
    add column if not exists position_type text not null default 'spot'
    check (position_type in ('spot', 'long', 'short'));

-- 3. Update strategy check constraint to allow futures_momentum
alter table public.trades drop constraint if exists trades_strategy_check;
alter table public.trades
    add constraint trades_strategy_check
    check (strategy in ('grid', 'momentum', 'arbitrage', 'futures_momentum'));

-- 4. Update strategy_log check constraint
alter table public.strategy_log drop constraint if exists strategy_log_strategy_selected_check;
alter table public.strategy_log
    add constraint strategy_log_strategy_selected_check
    check (strategy_selected in ('grid', 'momentum', 'arbitrage', 'futures_momentum', 'paused'));

-- 5. Indexes for the new columns
create index if not exists idx_trades_position_type on public.trades (position_type);
create index if not exists idx_trades_leverage      on public.trades (leverage)
    where leverage > 1;
