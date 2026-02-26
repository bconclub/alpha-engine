-- GPFC #3: Ensure bybit_balance and kraken_balance columns exist
-- Run this in the Supabase SQL Editor if you get:
--   "Could not find the 'bybit_balance' column of 'bot_status' in the schema cache"

ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS bybit_balance NUMERIC DEFAULT 0;
ALTER TABLE bot_status ADD COLUMN IF NOT EXISTS kraken_balance NUMERIC DEFAULT 0;

-- Reload PostgREST schema cache so Supabase sees the new columns immediately
NOTIFY pgrst, 'reload schema';
