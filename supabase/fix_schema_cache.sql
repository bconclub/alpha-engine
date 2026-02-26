-- fix_schema_cache.sql  —  Run in Supabase SQL Editor
-- Adds ALL columns the Alpha engine writes to bot_status.
-- Safe to run multiple times (IF NOT EXISTS).

-- ── Exchange balances & connectivity ──────────────────────────────────
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS binance_balance    NUMERIC;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS delta_balance      NUMERIC;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS delta_balance_inr  NUMERIC;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS bybit_balance      NUMERIC;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS kraken_balance     NUMERIC;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS binance_connected  BOOLEAN DEFAULT false;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS delta_connected    BOOLEAN DEFAULT false;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS bybit_connected    BOOLEAN DEFAULT false;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS kraken_connected   BOOLEAN DEFAULT false;

-- ── Strategy & exchange toggles ───────────────────────────────────────
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS scalp_enabled          BOOLEAN DEFAULT true;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS options_scalp_enabled   BOOLEAN DEFAULT false;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS bybit_enabled          BOOLEAN DEFAULT false;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS delta_enabled          BOOLEAN DEFAULT false;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS kraken_enabled         BOOLEAN DEFAULT false;

-- ── Runtime metrics ───────────────────────────────────────────────────
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS active_strategy_count  INTEGER DEFAULT 0;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS uptime_seconds         INTEGER DEFAULT 0;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS inr_usd_rate           NUMERIC;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS daily_pnl_scalp        NUMERIC DEFAULT 0;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS daily_pnl_options      NUMERIC DEFAULT 0;

-- ── Market regime data ────────────────────────────────────────────────
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS market_regime    TEXT;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS chop_score       NUMERIC;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS atr_ratio        NUMERIC;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS net_change_30m   NUMERIC;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS regime_since     TIMESTAMPTZ;

-- ── Diagnostics blob (JSONB) ──────────────────────────────────────────
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS diagnostics      JSONB;

-- ── Reload PostgREST schema cache so Supabase sees columns immediately
NOTIFY pgrst, 'reload schema';
