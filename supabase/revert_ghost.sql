-- ═══════════════════════════════════════════════════════════════════
-- REVERT incorrectly cancelled trades + fix wrong exit prices
-- ═══════════════════════════════════════════════════════════════════

-- Step 1: Revert ALL incorrectly cancelled "GHOST" trades back to closed
UPDATE trades
SET status = 'closed'
WHERE status = 'cancelled'
  AND exit_reason = 'GHOST';

-- Step 2: Fix exit prices with correct data from exchange fills
-- Delta ETH contract_size = 0.01, XRP contract_size = 1.0
-- Entry fee (limit/maker): 0.024% = 0.00024
-- Exit fee (taker): 0.059% = 0.00059

-- #1290: LONG ETH 2ct, entry=$1934.80, CORRECT exit=$1933.55
UPDATE trades SET
  exit_price  = 1933.55,
  gross_pnl   = round(2 * 0.01 * (1933.55 - 1934.80), 8),            -- -0.025
  entry_fee   = round(2 * 0.01 * 1934.80 * 0.00024, 8),              -- 0.009287
  exit_fee    = round(2 * 0.01 * 1933.55 * 0.00059, 8),              -- 0.022816
  pnl         = round(2 * 0.01 * (1933.55 - 1934.80)
                 - 2 * 0.01 * 1934.80 * 0.00024
                 - 2 * 0.01 * 1933.55 * 0.00059, 8),                 -- net
  pnl_pct     = round(
                 (2 * 0.01 * (1933.55 - 1934.80)
                  - 2 * 0.01 * 1934.80 * 0.00024
                  - 2 * 0.01 * 1933.55 * 0.00059)
                 / (2 * 0.01 * 1934.80 / 20) * 100, 4),             -- % on collateral
  exit_reason = 'POSITION_GONE'
WHERE id = 1290;

-- #1291: LONG XRP 26ct, entry=$1.3432, CORRECT exit=$1.3437 (avg of 1.3436/1.3438)
UPDATE trades SET
  exit_price  = 1.3437,
  gross_pnl   = round(26 * 1.0 * (1.3437 - 1.3432), 8),             -- 0.013
  entry_fee   = round(26 * 1.0 * 1.3432 * 0.00024, 8),              -- 0.008381
  exit_fee    = round(26 * 1.0 * 1.3437 * 0.00059, 8),              -- 0.020612
  pnl         = round(26 * 1.0 * (1.3437 - 1.3432)
                 - 26 * 1.0 * 1.3432 * 0.00024
                 - 26 * 1.0 * 1.3437 * 0.00059, 8),                 -- net
  pnl_pct     = round(
                 (26 * 1.0 * (1.3437 - 1.3432)
                  - 26 * 1.0 * 1.3432 * 0.00024
                  - 26 * 1.0 * 1.3437 * 0.00059)
                 / (26 * 1.0 * 1.3432 / 20) * 100, 4),             -- % on collateral
  exit_reason = 'POSITION_GONE'
WHERE id = 1291;

-- #1294: LONG ETH 3ct, entry=$1925.30, CORRECT exit=$1923.85
UPDATE trades SET
  exit_price  = 1923.85,
  gross_pnl   = round(3 * 0.01 * (1923.85 - 1925.30), 8),           -- -0.0435
  entry_fee   = round(3 * 0.01 * 1925.30 * 0.00024, 8),             -- 0.013862
  exit_fee    = round(3 * 0.01 * 1923.85 * 0.00059, 8),             -- 0.034052
  pnl         = round(3 * 0.01 * (1923.85 - 1925.30)
                 - 3 * 0.01 * 1925.30 * 0.00024
                 - 3 * 0.01 * 1923.85 * 0.00059, 8),                -- net
  pnl_pct     = round(
                 (3 * 0.01 * (1923.85 - 1925.30)
                  - 3 * 0.01 * 1925.30 * 0.00024
                  - 3 * 0.01 * 1923.85 * 0.00059)
                 / (3 * 0.01 * 1925.30 / 20) * 100, 4),            -- % on collateral
  exit_reason = 'POSITION_GONE'
WHERE id = 1294;
