-- ============================================================
-- Control Panel Tables: pair_config, setup_config, signal_state
-- Run this in Supabase SQL Editor
-- ============================================================

-- Pair configuration (dashboard <-> engine)
CREATE TABLE IF NOT EXISTS pair_config (
  pair TEXT PRIMARY KEY,
  enabled BOOLEAN DEFAULT true,
  allocation_pct INTEGER DEFAULT 20,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Seed data
INSERT INTO pair_config (pair, enabled, allocation_pct) VALUES
  ('BTC', true, 20),
  ('ETH', true, 30),
  ('XRP', true, 50),
  ('SOL', true, 20)
ON CONFLICT (pair) DO NOTHING;

-- Setup configuration (dashboard <-> engine)
CREATE TABLE IF NOT EXISTS setup_config (
  setup_type TEXT PRIMARY KEY,
  enabled BOOLEAN DEFAULT true,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Seed data (all enabled except VOL_DIVERGENCE at 14% WR)
INSERT INTO setup_config (setup_type, enabled) VALUES
  ('RSI_OVERRIDE', true),
  ('BB_SQUEEZE', true),
  ('LIQ_SWEEP', true),
  ('FVG_FILL', true),
  ('VOL_DIVERGENCE', false),
  ('VWAP_RECLAIM', true),
  ('TREND_CONT', true),
  ('MOMENTUM_BURST', true),
  ('MEAN_REVERT', true),
  ('MULTI_SIGNAL', true),
  ('MIXED', true)
ON CONFLICT (setup_type) DO NOTHING;

-- Signal state (engine -> dashboard, upserted each scan cycle)
CREATE TABLE IF NOT EXISTS signal_state (
  pair TEXT NOT NULL,
  signal_id TEXT NOT NULL,
  value DOUBLE PRECISION,
  threshold DOUBLE PRECISION,
  firing BOOLEAN DEFAULT false,
  direction TEXT DEFAULT 'neutral',
  updated_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (pair, signal_id)
);

-- Enable realtime for new tables
do $$ begin
    alter publication supabase_realtime add table pair_config;
exception when duplicate_object then null; end $$;
do $$ begin
    alter publication supabase_realtime add table setup_config;
exception when duplicate_object then null; end $$;
do $$ begin
    alter publication supabase_realtime add table signal_state;
exception when duplicate_object then null; end $$;
