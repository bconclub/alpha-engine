-- ============================================================================
-- QUICK FIX — Run this in Supabase SQL Editor RIGHT NOW
-- Ensures all columns exist and constraints allow all strategies
-- Safe to run multiple times (idempotent)
-- ============================================================================

-- ── 1. Add missing columns to strategy_log ──
alter table public.strategy_log add column if not exists exchange          text not null default 'binance';
alter table public.strategy_log add column if not exists signal_strength   numeric(10,4);
alter table public.strategy_log add column if not exists macd_value        numeric(20,8);
alter table public.strategy_log add column if not exists macd_signal       numeric(20,8);
alter table public.strategy_log add column if not exists macd_histogram    numeric(20,8);
alter table public.strategy_log add column if not exists current_price     numeric(20,8);
alter table public.strategy_log add column if not exists entry_distance_pct numeric(10,4);
alter table public.strategy_log add column if not exists plus_di           numeric(10,4);
alter table public.strategy_log add column if not exists minus_di          numeric(10,4);
alter table public.strategy_log add column if not exists bb_upper          numeric(20,8);
alter table public.strategy_log add column if not exists bb_lower          numeric(20,8);
alter table public.strategy_log add column if not exists price_change_15m  numeric(10,4);
alter table public.strategy_log add column if not exists price_change_1h   float8;
alter table public.strategy_log add column if not exists price_change_24h  float8;
alter table public.strategy_log add column if not exists direction         text default 'neutral';

-- ── 2. Add missing columns to bot_status ──
alter table public.bot_status add column if not exists binance_balance       numeric(20,8);
alter table public.bot_status add column if not exists delta_balance         numeric(20,8);
alter table public.bot_status add column if not exists delta_balance_inr     numeric(20,2);
alter table public.bot_status add column if not exists binance_connected     boolean not null default false;
alter table public.bot_status add column if not exists delta_connected       boolean not null default false;
alter table public.bot_status add column if not exists shorting_enabled      boolean not null default false;
alter table public.bot_status add column if not exists leverage              integer not null default 1;
alter table public.bot_status add column if not exists active_strategy_count integer not null default 0;
alter table public.bot_status add column if not exists uptime_seconds        integer not null default 0;

-- ── 3. Add missing columns to trades ──
alter table public.trades add column if not exists leverage      numeric(5,2) not null default 1;
alter table public.trades add column if not exists position_type text not null default 'spot';
alter table public.trades add column if not exists order_id      text;

-- ── 4. Fix strategy constraints (allow all strategies) ──
alter table public.trades drop constraint if exists trades_strategy_check;
alter table public.trades add constraint trades_strategy_check
    check (strategy in ('grid', 'momentum', 'arbitrage', 'futures_momentum', 'scalp', 'options_scalp'));

alter table public.strategy_log drop constraint if exists strategy_log_strategy_selected_check;
alter table public.strategy_log add constraint strategy_log_strategy_selected_check
    check (strategy_selected in ('grid', 'momentum', 'arbitrage', 'futures_momentum', 'scalp', 'options_scalp', 'paused'));

-- ── 5. Fix position_type constraint ──
alter table public.trades drop constraint if exists trades_position_type_check;
alter table public.trades add constraint trades_position_type_check
    check (position_type in ('spot', 'long', 'short'));

-- ── 6. Indexes ──
create index if not exists idx_strategy_log_exchange on public.strategy_log (exchange);
create index if not exists idx_strategy_log_created  on public.strategy_log (created_at desc);
create index if not exists idx_bot_status_created    on public.bot_status (created_at desc);

-- ── 7. Recreate views with all columns ──
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
    plus_di, minus_di, direction
from   public.strategy_log
order  by pair, timestamp desc;

create or replace view public.latest_strategy_log as
select distinct on (pair, exchange)
       *
from   public.strategy_log
order  by pair, exchange, timestamp desc;

-- ── 8. Anon RLS policies (dashboard uses anon key) ──
do $$ begin
    create policy "Allow anon read trades" on public.trades for select to anon using (true);
exception when duplicate_object then null;
end $$;

do $$ begin
    create policy "Allow anon read strategy_log" on public.strategy_log for select to anon using (true);
exception when duplicate_object then null;
end $$;

do $$ begin
    create policy "Allow anon read bot_status" on public.bot_status for select to anon using (true);
exception when duplicate_object then null;
end $$;

do $$ begin
    create policy "Allow anon read bot_commands" on public.bot_commands for select to anon using (true);
exception when duplicate_object then null;
end $$;

do $$ begin
    create policy "Allow anon insert bot_commands" on public.bot_commands for insert to anon with check (true);
exception when duplicate_object then null;
end $$;

-- ── 9. Close ghost/orphaned open trades ──
-- Any trade open for 2+ hours is stuck — the bot max hold is 30 min.
-- Mark them as cancelled so they stop showing as active positions.
update public.trades
set    status = 'cancelled',
       reason = coalesce(reason, '') || ' [auto-closed: orphaned]',
       closed_at = now()
where  status = 'open'
  and  opened_at < now() - interval '2 hours';

-- ── 10. Verify: check what pairs exist in strategy_log ──
select pair, exchange, count(*) as rows, max(created_at) as latest
from public.strategy_log
group by pair, exchange
order by pair;

-- ── 11. Verify: check remaining open trades (should only be real active ones) ──
select id, pair, side, entry_price, strategy, exchange, position_type, opened_at
from public.trades
where status = 'open'
order by opened_at desc;

-- ── 12. Verify: check P&L values on closed trades ──
-- If pnl is always 0 or NULL, the trade_executor isn't writing P&L correctly.
select id, pair, position_type, entry_price, exit_price,
       pnl, pnl_pct, status, reason, opened_at, closed_at
from public.trades
where status = 'closed'
order by closed_at desc nulls last
limit 20;

-- ── 13. Verify: check if any closed trades have NULL pnl (missed update) ──
select count(*) as closed_with_null_pnl
from public.trades
where status = 'closed'
  and pnl is null;

-- Done! If SOL/XRP rows appear above, the dashboard will show them.
-- If only BTC/ETH appear, the engine isn't logging SOL/XRP (check VPS logs).
