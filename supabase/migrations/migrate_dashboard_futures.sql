-- ============================================================================
-- DASHBOARD FUTURES VIEWS — Run in Supabase SQL Editor
-- Adds views for Delta Exchange / futures data visibility on the dashboard
-- ============================================================================

-- ============================================================================
-- 1. v_trades_today — Updated to include futures columns
-- ============================================================================
drop view if exists public.v_trades_today;

create or replace view public.v_trades_today as
select
    id,
    opened_at,
    closed_at,
    pair,
    side,
    entry_price,
    exit_price,
    amount,
    cost,
    strategy,
    order_type,
    exchange,
    pnl,
    pnl_pct,
    status,
    reason,
    leverage,
    position_type,
    -- Computed: effective value accounting for leverage
    round((cost * leverage)::numeric, 8) as effective_value
from   public.trades
where  opened_at >= (now() at time zone 'UTC')::date
order  by opened_at desc;


-- ============================================================================
-- 2. v_recent_win_rate — Updated: split by exchange (spot vs futures)
-- ============================================================================
drop view if exists public.v_recent_win_rate;

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
    -- Spot breakdown
    count(*) filter (where position_type = 'spot')    as spot_trades,
    round(sum(pnl) filter (where position_type = 'spot')::numeric, 8) as spot_pnl,
    -- Futures breakdown
    count(*) filter (where position_type in ('long', 'short')) as futures_trades,
    round(sum(pnl) filter (where position_type in ('long', 'short'))::numeric, 8) as futures_pnl
from (
    select pnl, position_type
    from   public.trades
    where  status = 'closed'
    order  by closed_at desc
    limit  50
) sub;


-- ============================================================================
-- 3. v_open_positions — NEW: all currently open positions with futures info
-- ============================================================================
create or replace view public.v_open_positions as
select
    id,
    opened_at,
    pair,
    side,
    entry_price,
    amount,
    cost,
    strategy,
    exchange,
    leverage,
    position_type,
    reason,
    order_id,
    round((cost * leverage)::numeric, 8) as effective_exposure
from   public.trades
where  status = 'open'
order  by opened_at desc;


-- ============================================================================
-- 4. v_futures_positions — NEW: only futures (long/short) positions
-- ============================================================================
create or replace view public.v_futures_positions as
select
    id,
    opened_at,
    closed_at,
    pair,
    side,
    entry_price,
    exit_price,
    amount,
    cost,
    leverage,
    position_type,
    pnl,
    pnl_pct,
    status,
    reason,
    -- Leveraged P&L
    round((pnl * leverage)::numeric, 8) as leveraged_pnl,
    round((pnl_pct * leverage)::numeric, 4) as leveraged_pnl_pct
from   public.trades
where  position_type in ('long', 'short')
order  by opened_at desc;


-- ============================================================================
-- 5. v_pnl_by_exchange — NEW: P&L breakdown by exchange (spot vs futures)
-- ============================================================================
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


-- ============================================================================
-- 6. v_pnl_by_pair — NEW: P&L breakdown by pair (across both exchanges)
-- ============================================================================
create or replace view public.v_pnl_by_pair as
select
    pair,
    exchange,
    position_type,
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


-- ============================================================================
-- 7. v_daily_pnl_timeseries — NEW: daily P&L over time for charting
-- ============================================================================
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
where  status = 'closed'
   and closed_at is not null
group  by trade_date, exchange
order  by trade_date desc;


-- ============================================================================
-- 8. v_strategy_performance — NEW: strategy-level performance stats
-- ============================================================================
create or replace view public.v_strategy_performance as
select
    strategy,
    exchange,
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
