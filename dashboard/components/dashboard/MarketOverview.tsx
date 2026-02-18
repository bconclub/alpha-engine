'use client';

import { useMemo } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { formatNumber, formatTimeAgo, cn } from '@/lib/utils';
import type { StrategyLog, OpenPosition } from '@/lib/types';

// ── Types ───────────────────────────────────────────────────────────────

interface AssetCard {
  asset: string;
  currentPrice: number | null;
  priceChange24h: number | null;
  priceChange1h: number | null;
  rsi: number | null;
  volumeRatio: number | null;
  bbPosition: string;
  activePosition: OpenPosition | null;
  lastTimestamp: string;
}

// ── Helpers ─────────────────────────────────────────────────────────────

function extractBaseAsset(pair: string): string {
  if (pair.includes('/')) return pair.split('/')[0];
  return pair.replace(/USD.*$/, '');
}

function determineBBPosition(
  price: number | null | undefined,
  upper: number | null | undefined,
  lower: number | null | undefined,
): string {
  if (price == null || upper == null || lower == null) return '—';
  const range = upper - lower;
  if (range <= 0) return 'Mid';
  const pos = (price - lower) / range;
  if (pos > 0.8) return 'Upper';
  if (pos < 0.2) return 'Lower';
  return 'Mid';
}

// ── Sub-components ──────────────────────────────────────────────────────

function PriceChange({ label, value }: { label: string; value: number | null }) {
  if (value == null) return <span className="text-zinc-600 text-[10px]">{label}: —</span>;
  const color = value >= 0 ? 'text-[#00c853]' : 'text-[#ff1744]';
  return (
    <span className={cn('text-[10px] font-mono', color)}>
      {label} {value >= 0 ? '+' : ''}{value.toFixed(2)}%
    </span>
  );
}

function AssetCardComponent({ card }: { card: AssetCard }) {
  const rsiColor = card.rsi != null
    ? card.rsi < 30 ? 'text-[#00c853]'
    : card.rsi > 70 ? 'text-[#ff1744]'
    : 'text-amber-400'
    : 'text-zinc-600';

  const h1 = card.priceChange1h;
  const trendArrow = h1 != null && h1 > 0.05 ? '\u2191'
    : h1 != null && h1 < -0.05 ? '\u2193'
    : '\u2192';
  const trendColor = h1 != null && h1 > 0.05 ? 'text-[#00c853]'
    : h1 != null && h1 < -0.05 ? 'text-[#ff1744]'
    : 'text-zinc-400';

  const pos = card.activePosition;
  const inTrade = pos != null;
  const positionSide = pos?.position_type;

  return (
    <div className={cn(
      'bg-zinc-900/60 border rounded-xl p-3 md:p-4',
      inTrade
        ? positionSide === 'short'
          ? 'border-[#ff1744]/20'
          : 'border-[#00c853]/20'
        : 'border-zinc-800',
    )}>
      {/* Header: Asset + trend */}
      <div className="flex items-center justify-between mb-2">
        <span className="text-base md:text-lg font-bold text-white">{card.asset}</span>
        <span className={cn('text-lg md:text-xl', trendColor)}>{trendArrow}</span>
      </div>

      {/* Price */}
      <div className="mb-1.5">
        <span className="text-xl md:text-2xl font-bold font-mono text-white">
          {card.currentPrice != null ? `$${formatNumber(card.currentPrice)}` : '—'}
        </span>
      </div>

      {/* Price changes */}
      <div className="flex flex-wrap gap-2 mb-3">
        <PriceChange label="24h" value={card.priceChange24h} />
        <PriceChange label="1h" value={card.priceChange1h} />
      </div>

      {/* Indicators */}
      <div className="grid grid-cols-3 gap-1.5 text-xs">
        <div>
          <span className="text-zinc-500 text-[10px] block">RSI</span>
          <span className={cn('font-mono font-medium text-xs', rsiColor)}>
            {card.rsi?.toFixed(0) ?? '—'}
          </span>
        </div>
        <div>
          <span className="text-zinc-500 text-[10px] block">Vol</span>
          <span className="font-mono text-zinc-300 text-xs">
            {card.volumeRatio != null ? `${card.volumeRatio.toFixed(1)}x` : '—'}
          </span>
        </div>
        <div>
          <span className="text-zinc-500 text-[10px] block">BB</span>
          <span className="font-mono text-zinc-300 text-xs">
            {card.bbPosition}
          </span>
        </div>
      </div>

      {/* Active position */}
      {pos && (
        <div className={cn(
          'mt-2 px-2 py-1 rounded text-[10px] font-medium truncate',
          pos.position_type === 'short'
            ? 'bg-[#ff1744]/10 text-[#ff1744]'
            : 'bg-[#00c853]/10 text-[#00c853]',
        )}>
          {pos.position_type === 'short' ? 'SHORT' : 'LONG'} @ ${formatNumber(pos.entry_price)}
        </div>
      )}
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────

export function MarketOverview() {
  const { strategyLog, openPositions } = useSupabase();

  const assetCards = useMemo(() => {
    const ACTIVE_ASSETS = new Set(['BTC', 'ETH', 'SOL', 'XRP']);

    // Group latest strategy_log by base asset, prefer Delta exchange
    const latestByAsset = new Map<string, StrategyLog>();
    for (const log of strategyLog) {
      if (!log.pair) continue;
      const asset = extractBaseAsset(log.pair);
      if (!ACTIVE_ASSETS.has(asset)) continue;
      const existing = latestByAsset.get(asset);
      if (!existing) {
        latestByAsset.set(asset, log);
      } else if (log.exchange === 'delta' && existing.exchange !== 'delta') {
        latestByAsset.set(asset, log);
      }
    }

    // Build cards
    const cards: AssetCard[] = [];
    for (const [asset, log] of Array.from(latestByAsset.entries())) {
      const price = log.current_price ?? null;
      const bbPos = determineBBPosition(price, log.bb_upper, log.bb_lower);
      const activePos = openPositions?.find(p => extractBaseAsset(p.pair) === asset) ?? null;

      cards.push({
        asset,
        currentPrice: price,
        priceChange24h: log.price_change_24h ?? null,
        priceChange1h: log.price_change_1h ?? null,
        rsi: log.rsi ?? null,
        volumeRatio: log.volume_ratio ?? null,
        bbPosition: bbPos,
        activePosition: activePos,
        lastTimestamp: log.timestamp,
      });
    }

    // Sort: BTC, ETH, SOL, XRP (fixed order for consistency)
    const order = ['BTC', 'ETH', 'SOL', 'XRP'];
    cards.sort((a, b) => {
      const ai = order.indexOf(a.asset);
      const bi = order.indexOf(b.asset);
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
    });
    return cards;
  }, [strategyLog, openPositions]);

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-3 md:p-5">
      <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">
        Market Overview
      </h3>

      {assetCards.length === 0 ? (
        <p className="text-sm text-zinc-500 text-center py-8">No market data yet</p>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 md:gap-3">
            {assetCards.map((card) => (
              <AssetCardComponent key={card.asset} card={card} />
            ))}
          </div>
          {assetCards[0]?.lastTimestamp && (
            <p className="text-[10px] text-zinc-600 mt-3">
              Last analysis: {formatTimeAgo(assetCards[0].lastTimestamp)}
            </p>
          )}
        </>
      )}
    </div>
  );
}
