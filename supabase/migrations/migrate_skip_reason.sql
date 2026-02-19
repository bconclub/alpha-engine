-- ============================================================================
-- Migration: Add skip_reason to strategy_log for dashboard skip reason display
-- Run in Supabase SQL Editor
-- ============================================================================

-- ── 1. STRATEGY_LOG: add skip_reason field ──
alter table public.strategy_log
    add column if not exists skip_reason text;
