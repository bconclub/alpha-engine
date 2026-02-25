-- Add momentum fade / dead momentum timer columns to trades table
-- These are written by the engine every ~10s while a position is open
-- and read by the dashboard to show countdown badges

ALTER TABLE public.trades
  ADD COLUMN IF NOT EXISTS fade_timer_active boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS fade_elapsed     integer,
  ADD COLUMN IF NOT EXISTS fade_required    integer,
  ADD COLUMN IF NOT EXISTS dead_timer_active boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS dead_elapsed     integer,
  ADD COLUMN IF NOT EXISTS dead_required    integer;

COMMENT ON COLUMN public.trades.fade_timer_active IS 'true when MOMENTUM_FADE confirmation timer is running';
COMMENT ON COLUMN public.trades.fade_elapsed      IS 'seconds elapsed in fade confirmation';
COMMENT ON COLUMN public.trades.fade_required     IS 'seconds required for fade confirmation (15 or 20 if trend-aligned)';
COMMENT ON COLUMN public.trades.dead_timer_active IS 'true when DEAD_MOMENTUM confirmation timer is running';
COMMENT ON COLUMN public.trades.dead_elapsed      IS 'seconds elapsed in dead momentum confirmation';
COMMENT ON COLUMN public.trades.dead_required     IS 'seconds required for dead momentum confirmation (20)';
