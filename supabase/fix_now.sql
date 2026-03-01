-- Exchange on/off toggles — persisted in bot_status
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS bybit_enabled boolean NOT NULL DEFAULT true;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS delta_enabled boolean NOT NULL DEFAULT true;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS kraken_enabled boolean NOT NULL DEFAULT true;

-- contracts column for trades (added in e827e6a but migration was missing)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS contracts numeric(20,8);
