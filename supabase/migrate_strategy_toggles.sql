-- ============================================================
-- Migration: Strategy toggles, INR rate, daily P&L breakdown
-- Run this in Supabase SQL Editor
-- ============================================================

-- 1. Add new columns to bot_status
ALTER TABLE public.bot_status
  ADD COLUMN IF NOT EXISTS scalp_enabled boolean NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS options_scalp_enabled boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS inr_usd_rate numeric(10,2),
  ADD COLUMN IF NOT EXISTS daily_pnl_scalp numeric(20,8) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS daily_pnl_options numeric(20,8) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS market_regime text,
  ADD COLUMN IF NOT EXISTS chop_score numeric(10,4),
  ADD COLUMN IF NOT EXISTS atr_ratio numeric(10,4),
  ADD COLUMN IF NOT EXISTS net_change_30m numeric(10,4),
  ADD COLUMN IF NOT EXISTS regime_since text;

-- 2. Fix bot_commands CHECK constraint to allow 'toggle_strategy'
ALTER TABLE public.bot_commands DROP CONSTRAINT IF EXISTS bot_commands_command_check;
ALTER TABLE public.bot_commands
  ADD CONSTRAINT bot_commands_command_check
  CHECK (command IN ('pause', 'resume', 'force_strategy', 'update_config', 'update_pair_config', 'close_trade', 'toggle_strategy'));

-- 3. Enable SOL pair
UPDATE pair_config SET enabled = true, allocation_pct = 20 WHERE pair = 'SOL';
