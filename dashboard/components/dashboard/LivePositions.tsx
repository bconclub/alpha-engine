'use client';

import { useMemo, useState, useCallback } from 'react';
import Link from 'next/link';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { useLivePrices } from '@/hooks/useLivePrices';
import { getSupabase } from '@/lib/supabase';
import { cn } from '@/lib/utils';
import {
  type PositionDisplay,
  TRAIL_ACTIVATION_PCT,
  fmtPrice,
  fmtPnl,
  getPositionState,
  StateBadge,
  PositionRangeBar,
} from './PositionBar';

// Delta contract sizes (must match engine)
const DELTA_CONTRACT_SIZE: Record<string, number> = {
  'BTC/USD:USD': 0.001,
  'ETH/USD:USD': 0.01,
  'SOL/USD:USD': 1.0,
  'XRP/USD:USD': 1.0,
};

const OPTION_SYMBOL_RE = /\d{6}-\d+-[CP]/;
function isOptionPosition(pos: { pair: string; strategy?: string }): boolean {
  return pos.strategy === 'options_scalp' || OPTION_SYMBOL_RE.test(pos.pair);
}

function getOptionSide(pair: string): 'CALL' | 'PUT' | null {
  if (pair.endsWith('-C')) return 'CALL';
  if (pair.endsWith('-P')) return 'PUT';
  return null;
}

function extractBaseAsset(pair: string): string {
  if (pair.includes('/')) return pair.split('/')[0];
  return pair.replace(/USD.*$/, '');
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function LivePositions() {
  const { openPositions, strategyLog, optionsState } = useSupabase();
  const livePrices = useLivePrices(openPositions.length > 0);

  // Track which positions are being closed
  const [closingIds, setClosingIds] = useState<Set<string>>(new Set());

  const handleClose = useCallback(async (posId: string, pair: string) => {
    const sb = getSupabase();
    if (!sb) return;

    setClosingIds((prev) => new Set(prev).add(posId));
    try {
      const { error } = await sb.from('bot_commands').insert({
        command: 'close_trade',
        params: { trade_id: Number(posId), pair },
      });
      if (error) {
        console.error('[Alpha] close_trade command failed:', error.message);
        setClosingIds((prev) => {
          const next = new Set(prev);
          next.delete(posId);
          return next;
        });
      }
      // Don't clear closingIds on success — wait for realtime to remove position
    } catch (e) {
      console.error('[Alpha] close_trade insert error:', e);
      setClosingIds((prev) => {
        const next = new Set(prev);
        next.delete(posId);
        return next;
      });
    }
  }, []);

  // Clear closing state when position disappears from openPositions
  const openIds = useMemo(() => new Set(openPositions.map((p) => p.id)), [openPositions]);
  useMemo(() => {
    setClosingIds((prev) => {
      const next = new Set<string>();
      Array.from(prev).forEach((id) => {
        if (openIds.has(id)) next.add(id);
      });
      return next.size !== prev.size ? next : prev;
    });
  }, [openIds]);

  // Build fallback prices from latest strategy_log entries (every ~5min)
  const fallbackPrices = useMemo(() => {
    const prices = new Map<string, number>();
    for (const log of strategyLog) {
      if (log.current_price && log.pair) {
        const asset = extractBaseAsset(log.pair);
        if (!prices.has(asset)) {
          prices.set(asset, log.current_price);
        }
      }
    }
    return prices;
  }, [strategyLog]);

  // Build display data for each open position
  const positions: PositionDisplay[] = useMemo(() => {
    if (!openPositions || openPositions.length === 0) return [];

    return openPositions.map((pos) => {
      const asset = extractBaseAsset(pos.pair);
      const isOption = isOptionPosition(pos);
      const leverage = pos.leverage > 1 ? pos.leverage : 1;

      // For options: use current_premium from options_state (NOT spot price!)
      // Spot price for ETH is ~$2000, but option premium is ~$11
      let currentPrice: number | null = null;
      if (isOption) {
        const pairKey = `${asset}/USD:USD`;
        const optState = optionsState.find((s) => s.pair === pairKey);
        currentPrice = optState?.current_premium ?? null;
      } else {
        // Priority: live API price (3s) → bot DB price (~10s) → strategy_log (~5min)
        currentPrice =
          livePrices.prices[pos.pair]
          ?? pos.current_price
          ?? fallbackPrices.get(asset)
          ?? null;
      }

      // Calculate P&L
      let pricePnlPct: number | null = null;
      let capitalPnlPct: number | null = null;
      let pnlUsd: number | null = null;
      let collateral: number | null = null;

      if (currentPrice != null && pos.entry_price > 0) {
        // Price move % (premium change for options, spot change for futures)
        if (pos.position_type === 'short') {
          pricePnlPct = ((pos.entry_price - currentPrice) / pos.entry_price) * 100;
        } else {
          pricePnlPct = ((currentPrice - pos.entry_price) / pos.entry_price) * 100;
        }
        // Capital return % = price move × leverage
        capitalPnlPct = pricePnlPct * leverage;

        // Dollar P&L (gross — fees deducted on close)
        // Options: 1 contract = 1 unit (no contract size conversion)
        let coinAmount = pos.amount;
        if (pos.exchange === 'delta' && !isOption) {
          const contractSize = DELTA_CONTRACT_SIZE[pos.pair] ?? 1.0;
          coinAmount = pos.amount * contractSize;
        }
        if (pos.position_type === 'short') {
          pnlUsd = (pos.entry_price - currentPrice) * coinAmount;
        } else {
          pnlUsd = (currentPrice - pos.entry_price) * coinAmount;
        }
        // Options: real wallet P&L = notional / leverage
        if (isOption && leverage > 1) pnlUsd = pnlUsd / leverage;

        // Collateral = notional / leverage
        const notional = pos.entry_price * coinAmount;
        collateral = leverage > 1 ? notional / leverage : notional;
      }

      // Use ACTUAL position state from bot (written to DB every ~10s)
      // Only trust trailing state if peak P&L confirms it (≥0.30%)
      const peakPnlPct = pos.peak_pnl ?? (pricePnlPct != null && pricePnlPct > 0 ? pricePnlPct : 0);
      const trailActive = (
        pos.position_state === 'trailing'
        && peakPnlPct >= TRAIL_ACTIVATION_PCT
      );

      // Use ACTUAL trail stop price from bot, or estimate if not available
      let trailStopPrice: number | null = pos.trail_stop_price ?? null;
      if (trailStopPrice == null && trailActive && currentPrice != null && pricePnlPct != null) {
        // Fallback estimation when bot hasn't written state yet
        let trailDist = 0.30;
        const tiers: [number, number][] = [[0.50, 0.30], [1.00, 0.50], [2.00, 0.70], [3.00, 1.00]];
        for (const [minProfit, dist] of tiers) {
          if (pricePnlPct >= minProfit) trailDist = dist;
        }
        if (pos.position_type === 'short') {
          trailStopPrice = currentPrice * (1 + trailDist / 100);
        } else {
          trailStopPrice = currentPrice * (1 - trailDist / 100);
        }
      }

      return {
        id: pos.id,
        pair: pos.pair,
        pairShort: asset,
        positionType: pos.position_type as 'long' | 'short',
        entryPrice: pos.entry_price,
        currentPrice,
        contracts: pos.amount,
        leverage,
        pricePnlPct,
        capitalPnlPct,
        pnlUsd,
        collateral,
        duration: durationSince(pos.opened_at),
        trailActive,
        trailStopPrice,
        peakPnlPct,
        slPrice: pos.stop_loss ?? null,
        tpPrice: pos.take_profit ?? null,
        exchange: pos.exchange,
        isOption,
        optionSide: isOption ? getOptionSide(pos.pair) : null,
      };
    });
  }, [openPositions, livePrices.prices, fallbackPrices, optionsState]);

  if (positions.length === 0) return null;

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-3 md:p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider">
          Live Positions
        </h3>
        <span className="text-xs text-zinc-500 font-mono flex items-center gap-1.5">
          {positions.length} active
          {livePrices.lastUpdated > 0 && (
            <span className="inline-flex items-center gap-1 text-[11px]">
              <span className="w-1.5 h-1.5 rounded-full bg-[#00c853] animate-pulse" />
              LIVE
            </span>
          )}
        </span>
      </div>

      <div className="space-y-2">
        {positions.map((pos) => {
          const isProfit = (pos.pricePnlPct ?? 0) >= 0;
          const pnlColor = isProfit ? 'text-[#00c853]' : 'text-[#ff1744]';
          const posState = getPositionState(pos);
          const borderColor = posState === 'trailing' ? 'border-[#00c853]/30'
            : posState === 'near_sl' || posState === 'at_risk' ? 'border-[#ff1744]/20'
            : isProfit ? 'border-[#00c853]/20'
            : 'border-zinc-800/50';
          const isClosing = closingIds.has(pos.id);

          return (
            <div
              key={pos.id}
              className={cn(
                'w-full bg-zinc-900/50 border rounded-lg px-3 py-2.5 md:px-4 md:py-3',
                'transition-colors',
                borderColor,
                isClosing && 'opacity-60',
              )}
            >
              {/* Row 1: Pair + Side + Status + Close + Duration */}
              <div className="flex items-center justify-between gap-2 mb-1.5">
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className={cn(
                      'w-1.5 h-5 rounded-sm shrink-0',
                      pos.positionType === 'short' ? 'bg-[#ff1744]' : 'bg-[#00c853]',
                    )}
                  />
                  <Link href="/trades" className="text-sm font-bold text-white hover:underline">
                    {pos.pairShort}
                  </Link>
                  {pos.isOption ? (
                    <>
                      <span className={cn(
                        'text-xs font-semibold px-1.5 py-0.5 rounded',
                        pos.optionSide === 'PUT'
                          ? 'bg-[#ff1744]/10 text-[#ff1744]'
                          : 'bg-[#00c853]/10 text-[#00c853]',
                      )}>
                        {pos.optionSide ?? 'OPT'}
                      </span>
                      <span className="text-[10px] font-mono text-pink-400/70 px-1 py-0.5 rounded bg-pink-400/5">
                        {pos.leverage}x<span className="text-[8px] ml-0.5">OPT</span>
                      </span>
                    </>
                  ) : (
                    <span className={cn(
                      'text-xs font-semibold px-1.5 py-0.5 rounded',
                      pos.positionType === 'short'
                        ? 'bg-[#ff1744]/10 text-[#ff1744]'
                        : 'bg-[#00c853]/10 text-[#00c853]',
                    )}>
                      {pos.positionType.toUpperCase()} {pos.leverage}x
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <StateBadge
                    state={posState}
                    trailStopPrice={pos.trailStopPrice}
                    entryPrice={pos.entryPrice}
                  />
                  <button
                    onClick={() => handleClose(pos.id, pos.pair)}
                    disabled={isClosing}
                    className={cn(
                      'px-2.5 py-1 rounded text-xs font-semibold transition-colors',
                      isClosing
                        ? 'bg-zinc-700/50 text-zinc-500 cursor-not-allowed'
                        : 'bg-[#ff1744]/10 text-[#ff1744] hover:bg-[#ff1744]/20 active:bg-[#ff1744]/30',
                    )}
                  >
                    {isClosing ? 'Closing...' : 'Close'}
                  </button>
                  <span className="text-[11px] text-zinc-500 font-mono">{pos.duration}</span>
                </div>
              </div>

              {/* Row 2: Labeled P&L grid */}
              {pos.pricePnlPct != null ? (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1 text-xs font-mono">
                  <div>
                    <div className="text-[11px] text-zinc-500 uppercase">P&L</div>
                    <div className={cn('font-bold', pnlColor)}>
                      {pos.pnlUsd != null ? `${pos.pnlUsd >= 0 ? '+' : '-'}${fmtPnl(pos.pnlUsd)}` : '\u2014'}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] text-zinc-500 uppercase">Return</div>
                    <div className={cn('font-bold', pnlColor)}>
                      {pos.capitalPnlPct != null ? `${pos.capitalPnlPct >= 0 ? '+' : ''}${pos.capitalPnlPct.toFixed(1)}%` : '\u2014'}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] text-zinc-500 uppercase">Entry &rarr; Now</div>
                    <div className="text-zinc-300">
                      ${fmtPrice(pos.entryPrice)} &rarr; ${pos.currentPrice != null ? fmtPrice(pos.currentPrice) : '...'}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] text-zinc-500 uppercase">Price Move</div>
                    <div className={cn(pnlColor)}>
                      {pos.pricePnlPct >= 0 ? '+' : ''}{pos.pricePnlPct.toFixed(pos.entryPrice < 10 ? 4 : 3)}%
                    </div>
                  </div>
                </div>
              ) : (
                <span className="text-xs text-zinc-500">Calculating...</span>
              )}

              {/* Row 3: Position range bar + position size */}
              <div className="flex items-center gap-3 mt-1.5">
                {pos.pricePnlPct != null && (
                  <div className="flex-1 max-w-[240px] md:max-w-[320px]">
                    <PositionRangeBar pos={pos} />
                  </div>
                )}
                <span className="text-xs font-mono text-zinc-500 whitespace-nowrap shrink-0">
                  {pos.contracts}{pos.exchange === 'delta' ? ' ct' : ''}
                  {pos.collateral != null && ` \u00b7 $${pos.collateral < 10 ? pos.collateral.toFixed(4) : pos.collateral.toFixed(2)}`}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function durationSince(timestamp: string): string {
  const ms = Date.now() - new Date(timestamp).getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return `${hours}h${remMins}m`;
}
