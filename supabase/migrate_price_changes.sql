-- ============================================================================
-- PRICE CHANGE COLUMNS MIGRATION — Run in Supabase SQL Editor
-- Adds price_change_1h and price_change_24h to strategy_log for Market Overview
-- ============================================================================

-- Add 1-hour price change column
alter table public.strategy_log add column if not exists price_change_1h float8;

-- Add 24-hour price change column
alter table public.strategy_log add column if not exists price_change_24h float8;
