-- ============================================================================
-- ALPHA TRADING BOT — Master Supabase Schema (all migrations applied)
-- ============================================================================
-- This file is the SINGLE SOURCE OF TRUTH for the database schema.
-- Run this on a FRESH Supabase project to set up everything from scratch.
-- For an EXISTING project, run only the idempotent migration at the bottom.
-- ============================================================================
-- Last updated: 2026-02-15
-- Engine: v3.3.x | Dashboard: v2.3.x
-- Active strategies: scalp, options_scalp
-- Pairs: BTC/USD:USD, ETH/USD:USD, SOL/USD:USD, XRP/USD:USD (Delta India)
-- ============================================================================


-- ############################################################################
-- SECTION 1: TABLES
-- ############################################################################

-- ============================================================================
-- 1. TRADES
-- Every buy/sell the bot executes. Entry rows are inserted on open, then
-- updated with exit_price / pnl / closed status when the position closes.
-- ============================================================================
create table if not exists public.trades (
    id            bigint generated always as identity primary key,
    created_at    timestamptz not null default now(),

    -- Timestamps
    opened_at     timestamptz not null default now(),
    closed_at     timestamptz,

    -- Instrument
    pair          text        not null,
    side          text        not null
                  check (side in ('buy', 'sell')),

    -- Prices
    entry_price   numeric(20,8) not null default 0,
    exit_price    numeric(20,8),
    amount        numeric(20,8) not null default 0,
    cost          numeric(20,8) not null default 0,

    -- Classification
    strategy      text        not null
                  check (strategy in ('grid', 'momentum', 'arbitrage', 'futures_momentum', 'scalp', 'options_scalp')),
    order_type    text        not null default 'market'
                  check (order_type in ('market', 'limit')),
    exchange      text        not null default 'binance',

    -- Result
    pnl           numeric(20,8) not null default 0,
    pnl_pct       numeric(10,4) not null default 0,
    status        text        not null default 'open'
                  check (status in ('open', 'closed', 'cancelled')),
    reason        text,

    -- Futures
    leverage      numeric(5,2) not null default 1,
    position_type text        not null default 'spot'
                  check (position_type in ('spot', 'long', 'short')),

    -- Exchange reference
    order_id      text
);

-- Indexes
create index if not exists idx_trades_opened_at     on public.trades (opened_at desc);
create index if not exists idx_trades_closed_at     on public.trades (closed_at desc) where closed_at is not null;
create index if not exists idx_trades_status        on public.trades (status);
create index if not exists idx_trades_strategy      on public.trades (strategy);
create index if not exists idx_trades_pair          on public.trades (pair);
create index if not exists idx_trades_pair_status   on public.trades (pair, status);
create index if not exists idx_trades_order_id      on public.trades (order_id) where order_id is not null;
create index if not exists idx_trades_position_type on public.trades (position_type);
create index if not exists idx_trades_leverage      on public.trades (leverage) where leverage > 1;


-- ============================================================================
-- 2. STRATEGY_LOG
-- Every analysis cycle logs which strategy was selected and why,
-- along with the raw indicator values that drove the decision.
-- ============================================================================
create table if not exists public.strategy_log (
    id                  bigint generated always as identity primary key,
    created_at          timestamptz not null default now(),
    timestamp           timestamptz not null default now(),

    -- Pair & exchange
    pair                text not null default 'BTC/USDT',
    exchange            text not null default 'binance',

    -- Market regime
    market_condition    text not null
                        check (market_condition in ('trending', 'sideways', 'volatile')),

    -- Decision
    strategy_selected   text not null
                        check (strategy_selected in ('grid', 'momentum', 'arbitrage', 'futures_momentum', 'scalp', 'options_scalp', 'paused')),
    reason              text,

    -- Core indicators
    adx                 numeric(10,4),
    atr                 numeric(20,8),
    bb_width            numeric(10,6),
    bb_upper            numeric(20,8),
    bb_lower            numeric(20,8),
    rsi                 numeric(10,4),
    volume_ratio        numeric(10,4),
    signal_strength     numeric(10,4),

    -- MACD
    macd_value          numeric(20,8),
    macd_signal         numeric(20,8),
    macd_histogram      numeric(20,8),

    -- Price data
    current_price       numeric(20,8),
    entry_distance_pct  numeric(10,4),
    price_change_15m    numeric(10,4),
    price_change_1h     float8,
    price_change_24h    float8,

    -- Directional indicators
    plus_di             numeric(10,4),
    minus_di            numeric(10,4),
    direction           text default 'neutral'   -- 'bullish', 'bearish', 'neutral'
);

-- Indexes
create index if not exists idx_strategy_log_ts        on public.strategy_log (timestamp desc);
create index if not exists idx_strategy_log_created    on public.strategy_log (created_at desc);
create index if not exists idx_strategy_log_condition  on public.strategy_log (market_condition);
create index if not exists idx_strategy_log_selected   on public.strategy_log (strategy_selected);
create index if not exists idx_strategy_log_pair       on public.strategy_log (pair);
create index if not exists idx_strategy_log_exchange   on public.strategy_log (exchange);


-- ============================================================================
-- 3. BOT_STATUS
-- Periodic heartbeat (every 2 min) so the dashboard always knows the bot's
-- current state. Also used for crash-recovery on restart.
-- ============================================================================
create table if not exists public.bot_status (
    id                    bigint generated always as identity primary key,
    created_at            timestamptz not null default now(),
    timestamp             timestamptz not null default now(),

    -- P&L
    total_pnl             numeric(20,8) not null default 0,
    daily_pnl             numeric(20,8) not null default 0,
    daily_loss_pct        numeric(10,4) not null default 0,

    -- Stats
    win_rate              numeric(10,4) not null default 0,
    total_trades          integer      not null default 0,
    open_positions        integer      not null default 0,

    -- Current state
    active_strategy       text,
    market_condition      text,
    capital               numeric(20,8) not null default 0,
    pair                  text         not null default 'BTC/USDT',

    -- Exchange balances
    binance_balance       numeric(20,8),
    delta_balance         numeric(20,8),
    delta_balance_inr     numeric(20,2),
    binance_connected     boolean      not null default false,
    delta_connected       boolean      not null default false,

    -- Bot state
    bot_state             text         not null default 'running'
        check (bot_state in ('running', 'paused', 'error')),
    shorting_enabled      boolean      not null default false,
    leverage              integer      not null default 1,
    active_strategy_count integer      not null default 0,
    uptime_seconds        integer      not null default 0,

    -- Flags
    is_running            boolean      not null default true,
    is_paused             boolean      not null default false,
    pause_reason          text
);

-- Indexes
create index if not exists idx_bot_status_ts      on public.bot_status (timestamp desc);
create index if not exists idx_bot_status_created on public.bot_status (created_at desc);
create index if not exists idx_bot_status_running on public.bot_status (is_running);


-- ============================================================================
-- 4. BOT_COMMANDS
-- Dashboard -> bot command queue. The dashboard inserts a row; the bot polls
-- for unexecuted commands, processes them, and marks executed = true.
-- ============================================================================
create table if not exists public.bot_commands (
    id            bigint generated always as identity primary key,
    created_at    timestamptz not null default now(),

    command       text not null
                  check (command in ('pause', 'resume', 'force_strategy', 'update_config', 'update_pair_config')),
    params        jsonb not null default '{}'::jsonb,

    executed      boolean     not null default false,
    executed_at   timestamptz,
    result        text
);

-- Indexes
create index if not exists idx_bot_commands_pending on public.bot_commands (executed, created_at asc) where executed = false;
create index if not exists idx_bot_commands_ts      on public.bot_commands (created_at desc);


-- ############################################################################
-- SECTION 2: ROW-LEVEL SECURITY
-- ############################################################################

alter table public.trades       enable row level security;
alter table public.strategy_log enable row level security;
alter table public.bot_status   enable row level security;
alter table public.bot_commands enable row level security;

-- Anon / authenticated: read-only for dashboard
do $$ begin
    create policy "Allow read access for authenticated users" on public.trades for select to authenticated using (true);
exception when duplicate_object then null;
end $$;

do $$ begin
    create policy "Allow read access for authenticated users" on public.strategy_log for select to authenticated using (true);
exception when duplicate_object then null;
end $$;

do $$ begin
    create policy "Allow read access for authenticated users" on public.bot_status for select to authenticated using (true);
exception when duplicate_object then null;
end $$;

do $$ begin
    create policy "Allow read access for authenticated users" on public.bot_commands for select to authenticated using (true);
exception when duplicate_object then null;
end $$;

do $$ begin
    create policy "Allow insert for authenticated users" on public.bot_commands for insert to authenticated with check (true);
exception when duplicate_object then null;
end $$;

-- Anon role: same read access (dashboard uses anon key)
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


-- ############################################################################
-- SECTION 3: REALTIME
-- ############################################################################

alter publication supabase_realtime add table public.trades;
alter publication supabase_realtime add table public.strategy_log;
alter publication supabase_realtime add table public.bot_status;
alter publication supabase_realtime add table public.bot_commands;


-- ############################################################################
-- SECTION 4: VIEWS
-- ############################################################################

-- Latest bot status (single row)
create or replace view public.v_bot_latest_status as
select *
from   public.bot_status
order  by timestamp desc
limit  1;

-- Today's trades
create or replace view public.v_trades_today as
select
    id, opened_at, closed_at, pair, side,
    entry_price, exit_price, amount, cost,
    strategy, order_type, exchange,
    pnl, pnl_pct, status, reason,
    leverage, position_type,
    round((cost * leverage)::numeric, 8) as effective_value
from   public.trades
where  opened_at >= (now() at time zone 'UTC')::date
order  by opened_at desc;

-- Win rate over last 50 closed trades
create or replace view public.v_recent_win_rate as
select
    count(*)                                          as total,
    count(*) filter (where pnl >= 0)                  as wins,
    count(*) filter (where pnl <  0)                  as losses,
    round(count(*) filter (where pnl >= 0)::numeric / greatest(count(*), 1) * 100, 2) as win_rate_pct,
    round(sum(pnl)::numeric, 8)                       as net_pnl,
    count(*) filter (where position_type = 'spot')    as spot_trades,
    round(sum(pnl) filter (where position_type = 'spot')::numeric, 8) as spot_pnl,
    count(*) filter (where position_type in ('long', 'short')) as futures_trades,
    round(sum(pnl) filter (where position_type in ('long', 'short'))::numeric, 8) as futures_pnl
from (
    select pnl, position_type
    from   public.trades
    where  status = 'closed'
    order  by closed_at desc
    limit  50
) sub;

-- Strategy distribution (last 24h)
create or replace view public.v_strategy_distribution_24h as
select
    pair, strategy_selected, market_condition,
    count(*)       as occurrences,
    min(timestamp) as first_seen,
    max(timestamp) as last_seen
from   public.strategy_log
where  timestamp >= now() - interval '24 hours'
group  by pair, strategy_selected, market_condition
order  by occurrences desc;

-- All currently open positions
create or replace view public.v_open_positions as
select
    id, opened_at, pair, side,
    entry_price, amount, cost,
    strategy, exchange, leverage, position_type,
    reason, order_id,
    round((cost * leverage)::numeric, 8) as effective_exposure
from   public.trades
where  status = 'open'
order  by opened_at desc;

-- Futures positions (open + closed)
create or replace view public.v_futures_positions as
select
    id, opened_at, closed_at, pair, side,
    entry_price, exit_price, amount, cost,
    leverage, position_type,
    pnl, pnl_pct, status, reason
from   public.trades
where  position_type in ('long', 'short')
order  by opened_at desc;

-- P&L by exchange
create or replace view public.v_pnl_by_exchange as
select
    exchange,
    count(*)                                          as total_trades,
    count(*) filter (where status = 'open')           as open_trades,
    count(*) filter (where status = 'closed')         as closed_trades,
    count(*) filter (where pnl >= 0 and status = 'closed') as wins,
    count(*) filter (where pnl <  0 and status = 'closed') as losses,
    round(count(*) filter (where pnl >= 0 and status = 'closed')::numeric
        / greatest(count(*) filter (where status = 'closed'), 1) * 100, 2) as win_rate_pct,
    round(coalesce(sum(pnl) filter (where status = 'closed'), 0)::numeric, 8) as total_pnl,
    round(coalesce(avg(pnl_pct) filter (where status = 'closed'), 0)::numeric, 4) as avg_pnl_pct
from   public.trades
group  by exchange
order  by exchange;

-- P&L by pair
create or replace view public.v_pnl_by_pair as
select
    pair, exchange, position_type,
    count(*)                                          as total_trades,
    count(*) filter (where status = 'closed')         as closed_trades,
    count(*) filter (where pnl >= 0 and status = 'closed') as wins,
    round(count(*) filter (where pnl >= 0 and status = 'closed')::numeric
        / greatest(count(*) filter (where status = 'closed'), 1) * 100, 2) as win_rate_pct,
    round(coalesce(sum(pnl) filter (where status = 'closed'), 0)::numeric, 8) as total_pnl,
    round(coalesce(sum(cost), 0)::numeric, 8)         as total_volume
from   public.trades
group  by pair, exchange, position_type
order  by total_pnl desc;

-- Daily P&L timeseries
create or replace view public.v_daily_pnl_timeseries as
select
    (closed_at at time zone 'UTC')::date              as trade_date,
    exchange,
    count(*)                                          as trades,
    round(sum(pnl)::numeric, 8)                       as daily_pnl,
    round(sum(pnl) filter (where position_type = 'spot')::numeric, 8) as spot_pnl,
    round(sum(pnl) filter (where position_type in ('long', 'short'))::numeric, 8) as futures_pnl,
    round(avg(pnl_pct)::numeric, 4)                   as avg_pnl_pct
from   public.trades
where  status = 'closed' and closed_at is not null
group  by trade_date, exchange
order  by trade_date desc;

-- Strategy performance stats
create or replace view public.v_strategy_performance as
select
    strategy, exchange,
    count(*)                                          as total_trades,
    count(*) filter (where status = 'closed')         as closed_trades,
    count(*) filter (where pnl >= 0 and status = 'closed') as wins,
    count(*) filter (where pnl <  0 and status = 'closed') as losses,
    round(count(*) filter (where pnl >= 0 and status = 'closed')::numeric
        / greatest(count(*) filter (where status = 'closed'), 1) * 100, 2) as win_rate_pct,
    round(coalesce(sum(pnl) filter (where status = 'closed'), 0)::numeric, 8) as total_pnl,
    round(coalesce(avg(pnl_pct) filter (where status = 'closed'), 0)::numeric, 4) as avg_pnl_pct,
    round(coalesce(max(pnl) filter (where status = 'closed'), 0)::numeric, 8) as best_trade,
    round(coalesce(min(pnl) filter (where status = 'closed'), 0)::numeric, 8) as worst_trade
from   public.trades
group  by strategy, exchange
order  by total_pnl desc;

-- Latest strategy snapshot per pair (for dashboard gauges)
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

-- Latest strategy log per pair+exchange
create or replace view public.latest_strategy_log as
select distinct on (pair, exchange)
       *
from   public.strategy_log
order  by pair, exchange, timestamp desc;


-- ############################################################################
-- SECTION 5: IDEMPOTENT MIGRATION (safe to run on existing DB)
-- Run this section on an EXISTING database to apply all missing changes.
-- Every statement uses IF NOT EXISTS / IF EXISTS so it's safe to re-run.
-- ############################################################################

-- ── trades: ensure all columns exist ──
alter table public.trades add column if not exists leverage      numeric(5,2) not null default 1;
alter table public.trades add column if not exists position_type text not null default 'spot';
alter table public.trades add column if not exists order_id      text;

-- ── trades: update strategy constraint to include all strategies ──
alter table public.trades drop constraint if exists trades_strategy_check;
alter table public.trades add constraint trades_strategy_check
    check (strategy in ('grid', 'momentum', 'arbitrage', 'futures_momentum', 'scalp', 'options_scalp'));

-- ── trades: ensure position_type constraint ──
-- (cannot use IF NOT EXISTS for CHECK, so drop+recreate)
alter table public.trades drop constraint if exists trades_position_type_check;
alter table public.trades add constraint trades_position_type_check
    check (position_type in ('spot', 'long', 'short'));

-- ── strategy_log: ensure all columns exist ──
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

-- ── strategy_log: update strategy constraint ──
alter table public.strategy_log drop constraint if exists strategy_log_strategy_selected_check;
alter table public.strategy_log add constraint strategy_log_strategy_selected_check
    check (strategy_selected in ('grid', 'momentum', 'arbitrage', 'futures_momentum', 'scalp', 'options_scalp', 'paused'));

-- ── bot_status: ensure all columns exist ──
alter table public.bot_status add column if not exists binance_balance       numeric(20,8);
alter table public.bot_status add column if not exists delta_balance         numeric(20,8);
alter table public.bot_status add column if not exists delta_balance_inr     numeric(20,2);
alter table public.bot_status add column if not exists binance_connected     boolean not null default false;
alter table public.bot_status add column if not exists delta_connected       boolean not null default false;
alter table public.bot_status add column if not exists shorting_enabled      boolean not null default false;
alter table public.bot_status add column if not exists leverage              integer not null default 1;
alter table public.bot_status add column if not exists active_strategy_count integer not null default 0;
alter table public.bot_status add column if not exists uptime_seconds        integer not null default 0;

-- ── Indexes (all IF NOT EXISTS — safe to re-run) ──
create index if not exists idx_strategy_log_exchange   on public.strategy_log (exchange);
create index if not exists idx_strategy_log_created    on public.strategy_log (created_at desc);
create index if not exists idx_bot_status_created      on public.bot_status (created_at desc);

-- Done! All tables, columns, constraints, views, indexes, RLS, and realtime are set up.
