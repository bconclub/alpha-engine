-- ============================================================================
-- ALPHA TRADING BOT — Supabase Schema
-- Run this in your Supabase SQL Editor (or as a migration)
-- ============================================================================

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
    pair          text        not null,               -- e.g. 'BTC/USDT'
    side          text        not null                 -- 'buy' or 'sell'
                  check (side in ('buy', 'sell')),

    -- Prices
    entry_price   numeric(20,8) not null default 0,
    exit_price    numeric(20,8),
    amount        numeric(20,8) not null default 0,   -- base-currency qty
    cost          numeric(20,8) not null default 0,    -- entry_price * amount (quote value)

    -- Classification
    strategy      text        not null                 -- 'grid', 'momentum', 'arbitrage', 'futures_momentum'
                  check (strategy in ('grid', 'momentum', 'arbitrage', 'futures_momentum')),
    order_type    text        not null default 'market'
                  check (order_type in ('market', 'limit')),
    exchange      text        not null default 'binance',

    -- Result
    pnl           numeric(20,8) not null default 0,
    pnl_pct       numeric(10,4) not null default 0,
    status        text        not null default 'open'
                  check (status in ('open', 'closed', 'cancelled')),
    reason        text,                                -- human-readable entry/exit reason

    -- Futures
    leverage      numeric(5,2) not null default 1,     -- 1 = spot, >1 = futures
    position_type text        not null default 'spot'  -- 'spot', 'long', 'short'
                  check (position_type in ('spot', 'long', 'short')),

    -- Exchange reference
    order_id      text                                 -- ccxt order id
);

-- Indexes: timestamp range scans, filtering by status/strategy/pair
create index if not exists idx_trades_opened_at   on public.trades (opened_at desc);
create index if not exists idx_trades_closed_at   on public.trades (closed_at desc)
    where closed_at is not null;
create index if not exists idx_trades_status      on public.trades (status);
create index if not exists idx_trades_strategy    on public.trades (strategy);
create index if not exists idx_trades_pair        on public.trades (pair);
create index if not exists idx_trades_pair_status on public.trades (pair, status);
create index if not exists idx_trades_order_id    on public.trades (order_id)
    where order_id is not null;
create index if not exists idx_trades_position_type on public.trades (position_type);
create index if not exists idx_trades_leverage      on public.trades (leverage)
    where leverage > 1;


-- ============================================================================
-- 2. STRATEGY_LOG
-- Every 5-minute analysis cycle logs which strategy was selected and why,
-- along with the raw indicator values that drove the decision.
-- ============================================================================
create table if not exists public.strategy_log (
    id                  bigint generated always as identity primary key,
    created_at          timestamptz not null default now(),
    timestamp           timestamptz not null default now(),

    -- Pair
    pair                text not null default 'BTC/USDT', -- e.g. 'BTC/USDT'

    -- Market regime
    market_condition    text not null                    -- 'trending', 'sideways', 'volatile'
                        check (market_condition in ('trending', 'sideways', 'volatile')),

    -- Decision
    strategy_selected   text not null                    -- 'grid', 'momentum', 'arbitrage', 'futures_momentum', 'paused'
                        check (strategy_selected in ('grid', 'momentum', 'arbitrage', 'futures_momentum', 'paused')),
    reason              text,

    -- Indicator snapshot (what the analyzer saw)
    adx                 numeric(10,4),
    atr                 numeric(20,8),
    bb_width            numeric(10,6),
    rsi                 numeric(10,4),
    volume_ratio        numeric(10,4)
);

-- Indexes: timeline queries, filtering by condition/strategy/pair
create index if not exists idx_strategy_log_ts         on public.strategy_log (timestamp desc);
create index if not exists idx_strategy_log_condition  on public.strategy_log (market_condition);
create index if not exists idx_strategy_log_selected   on public.strategy_log (strategy_selected);
create index if not exists idx_strategy_log_pair       on public.strategy_log (pair);


-- ============================================================================
-- 3. BOT_STATUS
-- Periodic heartbeat (every 5 min) so the dashboard always knows the bot's
-- current state.  Also used for crash-recovery on restart.
-- ============================================================================
create table if not exists public.bot_status (
    id                bigint generated always as identity primary key,
    created_at        timestamptz not null default now(),
    timestamp         timestamptz not null default now(),

    -- P&L
    total_pnl         numeric(20,8) not null default 0,
    daily_pnl         numeric(20,8) not null default 0,
    daily_loss_pct    numeric(10,4) not null default 0,

    -- Stats
    win_rate          numeric(10,4) not null default 0,
    total_trades      integer      not null default 0,
    open_positions    integer      not null default 0,

    -- Current state
    active_strategy   text,                               -- null = paused
    market_condition  text,                               -- last detected condition
    capital           numeric(20,8) not null default 0,
    pair              text         not null default 'BTC/USDT',

    -- Flags
    is_running        boolean      not null default true,
    is_paused         boolean      not null default false,
    pause_reason      text
);

-- Indexes: latest-status lookup, timeline
create index if not exists idx_bot_status_ts       on public.bot_status (timestamp desc);
create index if not exists idx_bot_status_running  on public.bot_status (is_running);


-- ============================================================================
-- 4. BOT_COMMANDS
-- Dashboard → bot command queue.  The dashboard inserts a row; the bot polls
-- for unexecuted commands, processes them, and marks executed = true.
-- ============================================================================
create table if not exists public.bot_commands (
    id            bigint generated always as identity primary key,
    created_at    timestamptz not null default now(),

    -- Command
    command       text not null                          -- 'pause', 'resume', 'force_strategy', 'update_config'
                  check (command in ('pause', 'resume', 'force_strategy', 'update_config')),
    params        jsonb not null default '{}'::jsonb,    -- e.g. {"strategy": "grid"} or {"pair": "ETH/USDT"}

    -- Execution tracking
    executed      boolean     not null default false,
    executed_at   timestamptz,
    result        text                                   -- success message or error
);

-- Indexes: pending-commands query (bot polls this), timeline
create index if not exists idx_bot_commands_pending on public.bot_commands (executed, created_at asc)
    where executed = false;
create index if not exists idx_bot_commands_ts      on public.bot_commands (created_at desc);


-- ============================================================================
-- ROW-LEVEL SECURITY
-- Enable RLS on all tables.  The service-role key bypasses RLS, so the bot
-- (using supabase-py with the service key) can read/write everything.
-- The anon key gets read-only on non-sensitive tables for the dashboard.
-- ============================================================================

alter table public.trades       enable row level security;
alter table public.strategy_log enable row level security;
alter table public.bot_status   enable row level security;
alter table public.bot_commands enable row level security;

-- Service role: full access (implicit — service key bypasses RLS)
-- Anon / authenticated role: read-only for dashboard display

create policy "Allow read access for authenticated users"
    on public.trades for select
    to authenticated using (true);

create policy "Allow read access for authenticated users"
    on public.strategy_log for select
    to authenticated using (true);

create policy "Allow read access for authenticated users"
    on public.bot_status for select
    to authenticated using (true);

-- bot_commands: dashboard can INSERT (to send commands) and SELECT
create policy "Allow read access for authenticated users"
    on public.bot_commands for select
    to authenticated using (true);

create policy "Allow insert for authenticated users"
    on public.bot_commands for insert
    to authenticated with check (true);


-- ============================================================================
-- REALTIME
-- Enable Supabase Realtime on all 4 tables so the dashboard gets live pushes.
-- ============================================================================

alter publication supabase_realtime add table public.trades;
alter publication supabase_realtime add table public.strategy_log;
alter publication supabase_realtime add table public.bot_status;
alter publication supabase_realtime add table public.bot_commands;


-- ============================================================================
-- HELPER VIEWS (optional — handy for dashboard queries)
-- ============================================================================

-- Latest bot status (single row)
create or replace view public.v_bot_latest_status as
select *
from   public.bot_status
order  by timestamp desc
limit  1;

-- Today's trades
create or replace view public.v_trades_today as
select *
from   public.trades
where  opened_at >= (now() at time zone 'UTC')::date
order  by opened_at desc;

-- Win rate over last 20 closed trades
create or replace view public.v_recent_win_rate as
select
    count(*)                                          as total,
    count(*) filter (where pnl >= 0)                  as wins,
    count(*) filter (where pnl <  0)                  as losses,
    round(
        count(*) filter (where pnl >= 0)::numeric
        / greatest(count(*), 1) * 100, 2
    )                                                 as win_rate_pct,
    round(sum(pnl)::numeric, 8)                       as net_pnl
from (
    select pnl
    from   public.trades
    where  status = 'closed'
    order  by closed_at desc
    limit  20
) sub;

-- Strategy distribution (last 24h) — grouped by pair
create or replace view public.v_strategy_distribution_24h as
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
