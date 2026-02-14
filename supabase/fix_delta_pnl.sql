-- ═══════════════════════════════════════════════════════════════════
-- Fix Delta Exchange P&L calculations (retroactive) — v2
--
-- Problems fixed:
--   1. P&L was calculated using raw contract count as if it were
--      coin amount.  1 contract ETH/USD = 0.01 ETH, not 1 ETH.
--   2. Leverage was stored as 5 (old config) — should be 20.
--   3. Fees were not deducted from P&L.
--   4. Cost column was wrong (raw notional instead of collateral).
--   5. bot_status table missing columns for leverage/balance display.
--
-- Correct formula:
--   LONG:  gross_pnl = (exit_price - entry_price) × 0.01 × contracts
--   SHORT: gross_pnl = (entry_price - exit_price) × 0.01 × contracts
--   entry_fee = entry_price × 0.01 × contracts × 0.0005
--   exit_fee  = exit_price  × 0.01 × contracts × 0.0005
--   net_pnl   = gross_pnl - entry_fee - exit_fee
--
-- Fee rate: 0.05% taker per side (0.0005 as decimal)
--
-- pnl_pct = return on COLLATERAL:
--   collateral = entry_price × contract_size × contracts / leverage
--   pnl_pct = net_pnl / collateral × 100
-- ═══════════════════════════════════════════════════════════════════

-- ─── Step 0: Ensure bot_status has all required columns ──────────
-- (idempotent — safe to run multiple times)
ALTER TABLE public.bot_status
    ADD COLUMN IF NOT EXISTS binance_balance   numeric(20,8),
    ADD COLUMN IF NOT EXISTS delta_balance     numeric(20,8),
    ADD COLUMN IF NOT EXISTS delta_balance_inr numeric(20,8),
    ADD COLUMN IF NOT EXISTS binance_connected boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS delta_connected   boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS bot_state         text NOT NULL DEFAULT 'running',
    ADD COLUMN IF NOT EXISTS shorting_enabled  boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS leverage          integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS active_strategy_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS uptime_seconds    integer NOT NULL DEFAULT 0;

-- ─── Step 1: Fix leverage from 5 → 20 for ALL Delta trades ───────
UPDATE trades
SET leverage = 20
WHERE exchange = 'delta'
  AND leverage != 20;

-- ─── Step 2: Fix LONG trades (ETH) ──────────────────────────────
-- contract_size = 0.01, fee = 0.05% per side, leverage = 20
UPDATE trades
SET
    pnl = (exit_price - entry_price) * 0.01 * amount
          - (entry_price * 0.01 * amount * 0.0005)
          - (exit_price * 0.01 * amount * 0.0005),
    pnl_pct = CASE
        WHEN leverage > 0 AND entry_price > 0
        THEN (
            (exit_price - entry_price) * 0.01 * amount
            - (entry_price * 0.01 * amount * 0.0005)
            - (exit_price * 0.01 * amount * 0.0005)
        ) / (entry_price * 0.01 * amount / leverage) * 100
        ELSE 0
    END
WHERE exchange = 'delta'
  AND position_type = 'long'
  AND status = 'closed'
  AND exit_price IS NOT NULL
  AND (pair LIKE 'ETH%' OR pair LIKE '%ETH%');

-- ─── Step 3: Fix SHORT trades (ETH) ─────────────────────────────
UPDATE trades
SET
    pnl = (entry_price - exit_price) * 0.01 * amount
          - (entry_price * 0.01 * amount * 0.0005)
          - (exit_price * 0.01 * amount * 0.0005),
    pnl_pct = CASE
        WHEN leverage > 0 AND entry_price > 0
        THEN (
            (entry_price - exit_price) * 0.01 * amount
            - (entry_price * 0.01 * amount * 0.0005)
            - (exit_price * 0.01 * amount * 0.0005)
        ) / (entry_price * 0.01 * amount / leverage) * 100
        ELSE 0
    END
WHERE exchange = 'delta'
  AND position_type = 'short'
  AND status = 'closed'
  AND exit_price IS NOT NULL
  AND (pair LIKE 'ETH%' OR pair LIKE '%ETH%');

-- ─── Step 4: Fix LONG trades (BTC) — contract_size = 0.001 ──────
UPDATE trades
SET
    pnl = (exit_price - entry_price) * 0.001 * amount
          - (entry_price * 0.001 * amount * 0.0005)
          - (exit_price * 0.001 * amount * 0.0005),
    pnl_pct = CASE
        WHEN leverage > 0 AND entry_price > 0
        THEN (
            (exit_price - entry_price) * 0.001 * amount
            - (entry_price * 0.001 * amount * 0.0005)
            - (exit_price * 0.001 * amount * 0.0005)
        ) / (entry_price * 0.001 * amount / leverage) * 100
        ELSE 0
    END
WHERE exchange = 'delta'
  AND position_type = 'long'
  AND status = 'closed'
  AND exit_price IS NOT NULL
  AND (pair LIKE 'BTC%' OR pair LIKE '%BTC%');

-- ─── Step 5: Fix SHORT trades (BTC) ─────────────────────────────
UPDATE trades
SET
    pnl = (entry_price - exit_price) * 0.001 * amount
          - (entry_price * 0.001 * amount * 0.0005)
          - (exit_price * 0.001 * amount * 0.0005),
    pnl_pct = CASE
        WHEN leverage > 0 AND entry_price > 0
        THEN (
            (entry_price - exit_price) * 0.001 * amount
            - (entry_price * 0.001 * amount * 0.0005)
            - (exit_price * 0.001 * amount * 0.0005)
        ) / (entry_price * 0.001 * amount / leverage) * 100
        ELSE 0
    END
WHERE exchange = 'delta'
  AND position_type = 'short'
  AND status = 'closed'
  AND exit_price IS NOT NULL
  AND (pair LIKE 'BTC%' OR pair LIKE '%BTC%');

-- ─── Step 6: Fix cost column (should be collateral) ─────────────
UPDATE trades
SET cost = entry_price * 0.01 * amount / GREATEST(leverage, 1)
WHERE exchange = 'delta'
  AND (pair LIKE 'ETH%' OR pair LIKE '%ETH%')
  AND entry_price IS NOT NULL;

UPDATE trades
SET cost = entry_price * 0.001 * amount / GREATEST(leverage, 1)
WHERE exchange = 'delta'
  AND (pair LIKE 'BTC%' OR pair LIKE '%BTC%')
  AND entry_price IS NOT NULL;

-- ─── Step 7: Zero out Binance dust trades ────────────────────────
UPDATE trades
SET pnl = 0, pnl_pct = 0
WHERE exchange = 'binance'
  AND status = 'closed';

-- Mark remaining Binance open trades as closed (dust)
UPDATE trades
SET status = 'closed', reason = 'dust_zeroed_v2'
WHERE exchange = 'binance'
  AND status = 'open';

-- ─── Step 8: Fix v_futures_positions view ────────────────────────
-- Must DROP first because we're removing columns (leveraged_pnl, leveraged_pnl_pct)
DROP VIEW IF EXISTS public.v_futures_positions;
CREATE OR REPLACE VIEW public.v_futures_positions AS
SELECT
    id, opened_at, closed_at, pair, side,
    entry_price, exit_price, amount, cost,
    leverage, position_type,
    pnl, pnl_pct, status, reason
FROM   public.trades
WHERE  position_type IN ('long', 'short')
ORDER  BY opened_at DESC;

-- ─── Step 9: Refresh v_bot_latest_status view ────────────────────
CREATE OR REPLACE VIEW public.v_bot_latest_status AS
SELECT *
FROM   public.bot_status
ORDER  BY timestamp DESC
LIMIT  1;

-- ─── Step 10: Fix the LATEST bot_status row with correct data ────
-- The bot writes total_pnl from memory (which was wrong). Fix it by
-- recalculating from the now-corrected trades table.
UPDATE bot_status
SET
    total_pnl = COALESCE((SELECT SUM(pnl) FROM trades WHERE status = 'closed'), 0),
    win_rate = COALESCE((
        SELECT ROUND(
            COUNT(*) FILTER (WHERE pnl > 0)::numeric
            / GREATEST(COUNT(*) FILTER (WHERE status = 'closed'), 1) * 100, 2
        )
        FROM trades WHERE status = 'closed'
    ), 0),
    total_trades = COALESCE((SELECT COUNT(*) FROM trades WHERE status = 'closed'), 0),
    leverage = 20,
    shorting_enabled = true
WHERE id = (SELECT id FROM bot_status ORDER BY timestamp DESC LIMIT 1);

-- ═══════════════════════════════════════════════════════════════════
-- Verify: After running, check the results:
-- SELECT pair, position_type, entry_price, exit_price, amount,
--        leverage, pnl, pnl_pct, cost
-- FROM trades
-- WHERE exchange = 'delta' AND status = 'closed'
-- ORDER BY opened_at DESC;
--
-- Expected for Trade 1 (buy 2069.55, sell 2073.85, 1 contract ETH):
--   gross = (2073.85 - 2069.55) * 0.01 * 1 = $0.0430
--   entry_fee = 2069.55 * 0.01 * 0.0005 = $0.01035
--   exit_fee  = 2073.85 * 0.01 * 0.0005 = $0.01037
--   net_pnl = 0.0430 - 0.01035 - 0.01037 = $0.0223
--   collateral = 2069.55 * 0.01 / 20 = $1.035
--   pnl_pct = 0.0223 / 1.035 * 100 = 2.15%
-- ═══════════════════════════════════════════════════════════════════
