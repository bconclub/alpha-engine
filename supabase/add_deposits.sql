-- ============================================================================
-- DEPOSITS — Track capital deposits into exchange accounts
-- Run this in your Supabase SQL Editor
-- ============================================================================

create table if not exists public.deposits (
    id          bigint generated always as identity primary key,
    created_at  timestamptz not null default now(),
    exchange    text not null,                           -- 'bybit', 'delta', 'kraken', 'binance'
    amount      numeric(20,8) not null default 0,        -- USD amount deposited
    amount_inr  numeric(20,2),                           -- INR amount (optional)
    notes       text                                     -- optional memo
);

-- Index for time-range queries
create index if not exists idx_deposits_created_at on public.deposits (created_at desc);

-- RLS: allow anon reads (dashboard) and inserts
alter table public.deposits enable row level security;
create policy "Allow anon read deposits" on public.deposits for select using (true);
create policy "Allow anon insert deposits" on public.deposits for insert with check (true);
