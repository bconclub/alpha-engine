-- Exchange on/off toggles — persisted in bot_status
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS bybit_enabled boolean NOT NULL DEFAULT true;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS delta_enabled boolean NOT NULL DEFAULT true;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS kraken_enabled boolean NOT NULL DEFAULT true;

-- contracts column for trades (added in e827e6a but migration was missing)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS contracts numeric(20,8);

-- Also fix gross_pnl for older trades that are missing it
UPDATE trades
SET gross_pnl = pnl + COALESCE(entry_fee, 0) + COALESCE(exit_fee, 0)
WHERE status = 'closed'
  AND gross_pnl = 0
  AND pnl != 0;

-- Rename legacy setup type
UPDATE trades SET setup_type = 'ANTIC' WHERE setup_type = 'TIER1_ANTICIPATORY';
