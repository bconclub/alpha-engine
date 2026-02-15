'use client';

import { useMemo } from 'react';
import Link from 'next/link';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { formatNumber, formatCurrency, cn } from '@/lib/utils';

// Delta contract sizes (must match engine)
const DELTA_CONTRACT_SIZE: Record<string, number> = {
  'BTC/USD:USD': 0.001,
  'ETH/USD:USD': 0.01,
  'SOL/USD:USD': 1.0,
  'XRP/USD:USD': 1.0,
};

function extractBaseAsset(pair: string): string {
  if (pair.includes('/')) return pair.split('/')[0];
  return pair.replace(/USD.*$/, '');
}

interface PositionDisplay {
  id: string;
  pair: string;
  pairShort: string;
  positionType: 'long' | 'short';
  entryPrice: number;
  currentPrice: number | null;
  contracts: number;
  leverage: number;
  pricePnlPct: number | null;    // raw price move %
  capitalPnlPct: number | null;  // leveraged capital return %
  pnlUsd: number | null;         // dollar P&L (gross, no fees)
  collateral: number | null;     // actual capital at risk
  duration: string;
  trailActive: boolean;
  trailStopPrice: number | null;
  slPrice: number | null;
  tpPrice: number | null;
  exchange: string;
}

export function LivePositions() {
  const { openPositions, strategyLog } = useSupabase();

  // Build current prices from latest strategy_log entries
  const currentPrices = useMemo(() => {
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
      const currentPrice = currentPrices.get(asset) ?? null;
      const leverage = pos.leverage > 1 ? pos.leverage : 1;

      // Calculate P&L
      let pricePnlPct: number | null = null;
      let capitalPnlPct: number | null = null;
      let pnlUsd: number | null = null;
      let collateral: number | null = null;

      if (currentPrice != null && pos.entry_price > 0) {
        // Price move %
        if (pos.position_type === 'short') {
          pricePnlPct = ((pos.entry_price - currentPrice) / pos.entry_price) * 100;
        } else {
          pricePnlPct = ((currentPrice - pos.entry_price) / pos.entry_price) * 100;
        }
        // Capital return % = price move × leverage
        capitalPnlPct = pricePnlPct * leverage;

        // Dollar P&L (gross — fees deducted on close)
        let coinAmount = pos.amount;
        if (pos.exchange === 'delta') {
          const contractSize = DELTA_CONTRACT_SIZE[pos.pair] ?? 1.0;
          coinAmount = pos.amount * contractSize;
        }
        if (pos.position_type === 'short') {
          pnlUsd = (pos.entry_price - currentPrice) * coinAmount;
        } else {
          pnlUsd = (currentPrice - pos.entry_price) * coinAmount;
        }

        // Collateral = notional / leverage
        const notional = pos.entry_price * coinAmount;
        collateral = leverage > 1 ? notional / leverage : notional;
      }

      // Determine trailing status:
      // If price P&L >= 0.50%, trail is likely active (matches engine TRAILING_ACTIVATE_PCT)
      const trailActive = pricePnlPct != null && pricePnlPct >= 0.50;

      // Estimate trail stop price based on dynamic tiers
      let trailStopPrice: number | null = null;
      if (trailActive && currentPrice != null && pricePnlPct != null) {
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
        slPrice: pos.stop_loss ?? null,
        tpPrice: pos.take_profit ?? null,
        exchange: pos.exchange,
      };
    });
  }, [openPositions, currentPrices]);

  if (positions.length === 0) return null;

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-3 md:p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider">
          Live Positions
        </h3>
        <span className="text-[10px] text-zinc-500 font-mono">
          {positions.length} active
        </span>
      </div>

      <div className="space-y-2">
        {positions.map((pos) => {
          const isProfit = (pos.pricePnlPct ?? 0) >= 0;
          const pnlColor = isProfit ? 'text-[#00c853]' : 'text-[#ff1744]';
          const bgColor = isProfit ? 'border-[#00c853]/20' : 'border-[#ff1744]/20';

          return (
            <Link
              key={pos.id}
              href="/trades"
              className={cn(
                'block w-full text-left bg-zinc-900/50 border rounded-lg px-3 py-2.5 md:px-4 md:py-3',
                'transition-colors hover:bg-zinc-800/50',
                bgColor,
              )}
            >
              {/* Row 1: Pair + Side + Status + Duration */}
              <div className="flex items-center justify-between gap-2 mb-1.5">
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className={cn(
                      'w-1.5 h-5 rounded-sm shrink-0',
                      pos.positionType === 'short' ? 'bg-[#ff1744]' : 'bg-[#00c853]',
                    )}
                  />
                  <span className="text-sm font-bold text-white">{pos.pairShort}</span>
                  <span className={cn(
                    'text-[10px] font-semibold px-1.5 py-0.5 rounded',
                    pos.positionType === 'short'
                      ? 'bg-[#ff1744]/10 text-[#ff1744]'
                      : 'bg-[#00c853]/10 text-[#00c853]',
                  )}>
                    {pos.positionType.toUpperCase()} {pos.leverage}x
                  </span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {pos.trailActive ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-[#00c853]/10 text-[#00c853]">
                      <span className="w-1.5 h-1.5 rounded-full bg-[#00c853] animate-pulse" />
                      TRAILING
                    </span>
                  ) : (pos.pricePnlPct ?? 0) < 0 ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-[#ff1744]/10 text-[#ff1744]">
                      HOLDING
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-400/10 text-amber-400">
                      HOLDING
                    </span>
                  )}
                  <span className="text-[9px] text-zinc-600 font-mono">{pos.duration}</span>
                </div>
              </div>

              {/* Row 2: Labeled P&L grid — clear what each number means */}
              {pos.pricePnlPct != null ? (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1 text-xs font-mono">
                  {/* P&L $ — the big number */}
                  <div>
                    <div className="text-[9px] text-zinc-500 uppercase">P&L</div>
                    <div className={cn('font-bold', pnlColor)}>
                      {pos.pnlUsd != null ? `${pos.pnlUsd >= 0 ? '+' : ''}${formatCurrency(pos.pnlUsd)}` : '—'}
                    </div>
                  </div>
                  {/* Capital return % — your actual ROI */}
                  <div>
                    <div className="text-[9px] text-zinc-500 uppercase">Return</div>
                    <div className={cn('font-bold', pnlColor)}>
                      {pos.capitalPnlPct != null ? `${pos.capitalPnlPct >= 0 ? '+' : ''}${pos.capitalPnlPct.toFixed(1)}%` : '—'}
                    </div>
                  </div>
                  {/* Entry → Now */}
                  <div>
                    <div className="text-[9px] text-zinc-500 uppercase">Entry → Now</div>
                    <div className="text-zinc-300">
                      ${formatNumber(pos.entryPrice)} → ${pos.currentPrice != null ? formatNumber(pos.currentPrice) : '...'}
                    </div>
                  </div>
                  {/* Price move % + size */}
                  <div>
                    <div className="text-[9px] text-zinc-500 uppercase">Price Move</div>
                    <div className={cn(pnlColor)}>
                      {pos.pricePnlPct >= 0 ? '+' : ''}{pos.pricePnlPct.toFixed(3)}%
                    </div>
                  </div>
                </div>
              ) : (
                <span className="text-xs text-zinc-500">Calculating...</span>
              )}

              {/* Row 3: Trail info + position size */}
              <div className="flex items-center gap-3 mt-1.5 text-[10px] font-mono text-zinc-500">
                {pos.trailActive && pos.trailStopPrice != null && (
                  <span>
                    trail stop: <span className="text-zinc-300">${formatNumber(pos.trailStopPrice)}</span>
                  </span>
                )}
                <span>
                  {pos.contracts}{pos.exchange === 'delta' ? ' ct' : ''}
                  {pos.collateral != null && ` · $${pos.collateral.toFixed(2)} collateral`}
                </span>
              </div>
            </Link>
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
