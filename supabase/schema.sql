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
    strategy      text        not null                 -- 'grid', 'momentum', 'arbitrage', 'futures_momentum', 'scalp', 'options_scalp'
                  check (strategy in ('grid', 'momentum', 'arbitrage', 'futures_momentum', 'scalp', 'options_scalp')),
    order_type    text        not null default 'market'
                  check (order_type in ('market', 'limit')),
    exchange      text        not null default 'binance',

    -- Result
    pnl           numeric(20,8) not null default 0,   -- NET P&L (after fees)
    pnl_pct       numeric(10,4) not null default 0,
    gross_pnl     numeric(20,8) not null default 0,   -- GROSS P&L (before fees)
    entry_fee     numeric(20,8) not null default 0,   -- fee paid on entry
    exit_fee      numeric(20,8) not null default 0,   -- fee paid on exit
    status        text        not null default 'open'
                  check (status in ('open', 'closed', 'cancelled')),
    reason        text,                                -- human-readable entry/exit reason
    exit_reason   text,                                -- clean exit type: TRAIL, SL, FLAT, MANUAL, etc.

    -- Futures / Options
    leverage      numeric(5,2) not null default 1,     -- 1 = spot, >1 = futures/options
    position_type text        not null default 'spot'  -- 'spot', 'long', 'short'
                  check (position_type in ('spot', 'long', 'short')),
    collateral    numeric(20,8),                        -- margin posted (notional/leverage)

    -- Exchange reference
    order_id      text,                                -- ccxt order id

    -- Setup tracking (v6.2)
    setup_type    text,                                -- PRIMARY setup classification (VWAP_RECLAIM, MOMENTUM_BURST, etc.)
    signals_fired text,                                -- all signal tags that fired (e.g. "MOM:+0.35% VOL:2.3x RSI:28<40")

    -- Live position state (written by bot every ~10s while position is open)
    position_state  text,                              -- 'holding' or 'trailing'
    trail_stop_price numeric(20,8),                    -- current trailing stop price
    current_pnl     numeric(10,4),                     -- unrealized P&L % (price move)
    current_price   numeric(20,8),                     -- latest price seen by bot
    peak_pnl        numeric(10,4),                     -- highest P&L % reached
    stop_loss       numeric(20,8),                     -- current stop loss price
    take_profit     numeric(20,8),                     -- current take profit price

    -- Slippage tracking (v3.4)
    slippage_pct    numeric(10,4),                     -- fill vs expected price slippage %
    slippage_flag   boolean not null default false      -- true when slippage > 2% (anomalous)
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
create index if not exists idx_trades_setup_type   on public.trades (setup_type)
    where setup_type is not null;


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
    strategy_selected   text not null                    -- 'grid', 'momentum', 'arbitrage', 'futures_momentum', 'scalp', 'paused'
                        check (strategy_selected in ('grid', 'momentum', 'arbitrage', 'futures_momentum', 'scalp', 'paused')),
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

    -- Exchange balances
    binance_balance   numeric(20,8),
    delta_balance     numeric(20,8),
    delta_balance_inr numeric(20,8),
    binance_connected boolean      not null default false,
    delta_connected   boolean      not null default false,

    -- Bot state
    bot_state         text         not null default 'running'
        check (bot_state in ('running', 'paused', 'error')),
    shorting_enabled  boolean      not null default false,
    leverage          integer      not null default 1,
    active_strategy_count integer  not null default 0,
    uptime_seconds    integer      not null default 0,

    -- Flags
    is_running        boolean      not null default true,
    is_paused         boolean      not null default false,
    pause_reason      text,

    -- Strategy toggles
    scalp_enabled          boolean not null default true,
    options_scalp_enabled  boolean not null default false,

    -- INR exchange rate
    inr_usd_rate      numeric(10,2),

    -- Daily P&L breakdown
    daily_pnl_scalp   numeric(20,8) not null default 0,
    daily_pnl_options  numeric(20,8) not null default 0,

    -- Market regime
    market_regime     text,
    chop_score        numeric(10,4),
    atr_ratio         numeric(10,4),
    net_change_30m    numeric(10,4),
    regime_since      text,

    -- Diagnostics (JSONB blob with per-pair skip reasons, cooldowns, signals)
    diagnostics       jsonb
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
    command       text not null                          -- 'pause', 'resume', 'force_strategy', 'update_config', 'update_pair_config', 'close_trade', 'toggle_strategy'
                  check (command in ('pause', 'resume', 'force_strategy', 'update_config', 'update_pair_config', 'close_trade', 'toggle_strategy')),
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

-- Today's trades (includes futures columns + effective value)
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

-- Win rate over last 50 closed trades (split by spot vs futures)
create or replace view public.v_recent_win_rate as
select
    count(*)                                          as total,
    count(*) filter (where pnl >= 0)                  as wins,
    count(*) filter (where pnl <  0)                  as losses,
    round(
        count(*) filter (where pnl >= 0)::numeric
        / greatest(count(*), 1) * 100, 2
    )                                                 as win_rate_pct,
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

-- All currently open positions (spot + futures) with live state
create or replace view public.v_open_positions as
select
    id, opened_at, pair, side,
    entry_price, amount, cost,
    strategy, exchange, leverage, position_type,
    reason, order_id,
    round((cost * leverage)::numeric, 8) as effective_exposure,
    stop_loss, take_profit,
    position_state,
    trail_stop_price,
    current_pnl,
    current_price,
    peak_pnl
from   public.trades
where  status = 'open'
order  by opened_at desc;

-- Futures positions only (open + closed)
-- NOTE: pnl is already the actual dollar P&L (no need to multiply by leverage).
-- pnl_pct is return on collateral (already accounts for leverage).
create or replace view public.v_futures_positions as
select
    id, opened_at, closed_at, pair, side,
    entry_price, exit_price, amount, cost,
    leverage, position_type,
    pnl, pnl_pct, status, reason
from   public.trades
where  position_type in ('long', 'short')
order  by opened_at desc;

-- P&L breakdown by exchange
create or replace view public.v_pnl_by_exchange as
select
    exchange,
    count(*)                                          as total_trades,
    count(*) filter (where status = 'open')           as open_trades,
    count(*) filter (where status = 'closed')         as closed_trades,
    count(*) filter (where pnl >= 0 and status = 'closed') as wins,
    count(*) filter (where pnl <  0 and status = 'closed') as losses,
    round(
        count(*) filter (where pnl >= 0 and status = 'closed')::numeric
        / greatest(count(*) filter (where status = 'closed'), 1) * 100, 2
    )                                                 as win_rate_pct,
    round(coalesce(sum(pnl) filter (where status = 'closed'), 0)::numeric, 8) as total_pnl,
    round(coalesce(avg(pnl_pct) filter (where status = 'closed'), 0)::numeric, 4) as avg_pnl_pct
from   public.trades
group  by exchange
order  by exchange;

-- P&L breakdown by pair
create or replace view public.v_pnl_by_pair as
select
    pair, exchange, position_type,
    count(*)                                          as total_trades,
    count(*) filter (where status = 'closed')         as closed_trades,
    count(*) filter (where pnl >= 0 and status = 'closed') as wins,
    round(
        count(*) filter (where pnl >= 0 and status = 'closed')::numeric
        / greatest(count(*) filter (where status = 'closed'), 1) * 100, 2
    )                                                 as win_rate_pct,
    round(coalesce(sum(pnl) filter (where status = 'closed'), 0)::numeric, 8) as total_pnl,
    round(coalesce(sum(cost), 0)::numeric, 8)         as total_volume
from   public.trades
group  by pair, exchange, position_type
order  by total_pnl desc;

-- Daily P&L timeseries for charts
create or replace view public.v_daily_pnl_timeseries as
select
    (closed_at at time zone 'UTC')::date              as trade_date,
    exchange,
    count(*)                                          as trades,
    round(sum(pnl)::numeric, 8)                       as daily_pnl,
    round(sum(pnl) filter (where position_type = 'spot')::numeric, 8)  as spot_pnl,
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
    round(
        count(*) filter (where pnl >= 0 and status = 'closed')::numeric
        / greatest(count(*) filter (where status = 'closed'), 1) * 100, 2
    )                                                 as win_rate_pct,
    round(coalesce(sum(pnl) filter (where status = 'closed'), 0)::numeric, 8) as total_pnl,
    round(coalesce(avg(pnl_pct) filter (where status = 'closed'), 0)::numeric, 4) as avg_pnl_pct,
    round(coalesce(max(pnl) filter (where status = 'closed'), 0)::numeric, 8) as best_trade,
    round(coalesce(min(pnl) filter (where status = 'closed'), 0)::numeric, 8) as worst_trade
from   public.trades
group  by strategy, exchange
order  by total_pnl desc;
