-- Migration: Add collateral column to trades table + fix strategy CHECK
-- Date: 2026-02-21
-- Purpose: Store collateral (margin posted) per trade for options display.
--          Also add 'options_scalp' to strategy CHECK constraint.

-- ─── Step 1: Add collateral column ────────────────────────────
ALTER TABLE public.trades
  ADD COLUMN IF NOT EXISTS collateral numeric(20,8);

COMMENT ON COLUMN public.trades.collateral IS
  'Margin/collateral posted for this trade. '
  'Futures: notional / leverage. '
  'Options: premium / leverage (exchange margin). '
  'Spot: same as cost (full notional).';

-- ─── Step 2: Fix strategy CHECK constraint ────────────────────
-- The original CHECK didn't include 'options_scalp'.
-- Drop old constraint and recreate with full list.
ALTER TABLE public.trades DROP CONSTRAINT IF EXISTS trades_strategy_check;
ALTER TABLE public.trades ADD CONSTRAINT trades_strategy_check
  CHECK (strategy IN ('grid', 'momentum', 'arbitrage', 'futures_momentum', 'scalp', 'options_scalp'));

-- ─── Step 3: Backfill collateral for existing trades ──────────
-- Futures/Spot: collateral = cost (already stored correctly)
UPDATE public.trades
SET collateral = cost
WHERE collateral IS NULL
  AND strategy != 'options_scalp';

-- Options: collateral = entry_price * amount / leverage
-- (entry_price = premium, amount = contracts, leverage = 50)
UPDATE public.trades
SET collateral = CASE
      WHEN leverage > 0 THEN entry_price * amount / leverage
      ELSE entry_price * amount
    END
WHERE collateral IS NULL
  AND strategy = 'options_scalp';

-- ─── Step 4: Fix broken options P&L for old trades ────────────
-- Trades recorded before the is_option_symbol fix had:
--   coin_amount = contracts * 0.01 (ETH contract size) instead of contracts * 1
--   gross_pnl was wrong by factor of 0.01
-- Recalculate gross_pnl, net_pnl, pnl_pct for options trades
-- where gross_pnl looks suspiciously small (< $0.01 when premium diff > $0.10)

-- Recalculate all options trades from scratch using stored entry/exit premiums.
-- Dollar amounts are REAL wallet P&L (notional ÷ leverage).
-- At 50x: BTC $95→$68 notional=-$27, real wallet loss=-$27/50=-$0.54.
-- pnl_pct stays vs collateral: -$0.54 / $1.90 * 100 = -28.42%
UPDATE public.trades
SET
  gross_pnl = (exit_price - entry_price) * amount / leverage,  -- real wallet gross
  entry_fee = COALESCE(entry_fee, 0) / CASE WHEN leverage > 0 THEN leverage ELSE 1 END,
  exit_fee = COALESCE(exit_fee, 0) / CASE WHEN leverage > 0 THEN leverage ELSE 1 END,
  pnl = (exit_price - entry_price) * amount / leverage
        - COALESCE(entry_fee, 0) / CASE WHEN leverage > 0 THEN leverage ELSE 1 END
        - COALESCE(exit_fee, 0) / CASE WHEN leverage > 0 THEN leverage ELSE 1 END,
  pnl_pct = CASE
    WHEN entry_price > 0 AND leverage > 0 THEN
      (
        (exit_price - entry_price) * amount / leverage
        - COALESCE(entry_fee, 0) / CASE WHEN leverage > 0 THEN leverage ELSE 1 END
        - COALESCE(exit_fee, 0) / CASE WHEN leverage > 0 THEN leverage ELSE 1 END
      ) / (entry_price * amount / leverage) * 100
    ELSE 0
  END
WHERE strategy = 'options_scalp'
  AND status = 'closed'
  AND exit_price IS NOT NULL;
