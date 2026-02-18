'use client';

import { useMemo } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { formatNumber, formatTimeAgo, cn } from '@/lib/utils';
import type { StrategyLog, OpenPosition, SignalState } from '@/lib/types';

// ── Types ───────────────────────────────────────────────────────────────

/** Per-direction signal dot state (core 4: MOM, VOL, RSI, BB) */
interface DirectionSignals {
  mom: boolean;
  vol: boolean;
  rsi: boolean;
  bb: boolean;
  count: number;
}

interface AssetCard {
  asset: string;
  currentPrice: number | null;
  priceChange24h: number | null;
  priceChange1h: number | null;
  priceChange15m: number | null;
  direction: string | null;
  rsi: number | null;
  volumeRatio: number | null;
  bbPosition: string;
  activePosition: OpenPosition | null;
  lastTimestamp: string;
  // Directional signal data (Fix 3)
  bullSignals: DirectionSignals;
  bearSignals: DirectionSignals;
  // Entry signals for in-trade display (Fix 2)
  entrySignals: DirectionSignals | null;
  entryDirection: 'long' | 'short' | null;
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

/**
 * Build directional signals from signal_state rows for a given pair.
 * Each signal has a `direction` ('bull' | 'bear' | 'neutral') and `firing` boolean.
 */
function buildDirectionSignals(
  pairSignals: SignalState[],
  dir: 'bull' | 'bear',
): DirectionSignals {
  const mom = pairSignals.some(
    s => (s.signal_id === 'MOM_60S' || s.signal_id === 'MOM_5M') && s.firing && s.direction === dir,
  );
  const vol = pairSignals.some(
    s => s.signal_id === 'VOL' && s.firing && s.direction === dir,
  );
  const rsi = pairSignals.some(
    s => s.signal_id === 'RSI' && s.firing && s.direction === dir,
  );
  const bb = pairSignals.some(
    s => (s.signal_id === 'BB' || s.signal_id === 'BBSQZ') && s.firing && s.direction === dir,
  );
  return { mom, vol, rsi, bb, count: +mom + +vol + +rsi + +bb };
}

/**
 * Parse entry signals from Trade.reason string.
 * Format: "LONG 3/4: MOM:+0.15% + VOL:1.5x + RSI:38<40 [15m=bullish]"
 * or      "SHORT 3/4: MOM:-0.20% + VOL:2.1x + BB:high@85% [15m=bearish]"
 */
function parseEntrySignals(reason: string | undefined): DirectionSignals | null {
  if (!reason) return null;
  const upper = reason.toUpperCase();
  const mom = upper.includes('MOM:') || upper.includes('MOM5M:');
  const vol = upper.includes('VOL:');
  const rsi = upper.includes('RSI:') || upper.includes('RSI-OVERRIDE');
  const bb = upper.includes('BB:') || upper.includes('BBSQZ:');
  const count = +mom + +vol + +rsi + +bb;
  if (count === 0) return null;
  return { mom, vol, rsi, bb, count };
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

/** Single signal dot with directional coloring */
function SignalDot({
  active,
  label,
  variant,
  outlined,
}: {
  active: boolean;
  label: string;
  variant: 'bull' | 'bear';
  outlined?: boolean;
}) {
  const activeColor = variant === 'bear' ? '#ff1744' : '#00c853';
  return (
    <div className="flex flex-col items-center gap-0.5">
      <div
        className={cn(
          'w-2.5 h-2.5 rounded-full border transition-all duration-500',
          active && !outlined
            ? `shadow-[0_0_4px_rgba(${variant === 'bear' ? '255,23,68' : '0,200,83'},0.4)]`
            : '',
        )}
        style={
          active
            ? outlined
              ? { backgroundColor: 'transparent', borderColor: activeColor, borderWidth: 2 }
              : { backgroundColor: activeColor, borderColor: activeColor }
            : { backgroundColor: '#27272a', borderColor: '#3f3f46' }
        }
      />
      <span
        className={cn(
          'text-[7px] font-mono leading-none',
          active ? (outlined ? 'text-zinc-500' : `text-[${activeColor}]`) : 'text-zinc-600',
        )}
        style={active && !outlined ? { color: activeColor } : undefined}
      >
        {label}
      </span>
    </div>
  );
}

/** A row of 4 signal dots for one direction */
function SignalRow({
  label,
  signals,
  variant,
  outlined,
  prefix,
}: {
  label: string;
  signals: DirectionSignals;
  variant: 'bull' | 'bear';
  outlined?: boolean;
  prefix?: string;
}) {
  const color = variant === 'bear' ? '#ff1744' : '#00c853';
  const countColor = signals.count >= 3
    ? `text-[${color}]`
    : signals.count >= 1 ? 'text-[#ffd600]' : 'text-zinc-600';

  return (
    <div className="flex items-center gap-1.5">
      <span
        className="text-[8px] font-mono w-7 shrink-0"
        style={signals.count > 0 ? { color } : { color: '#52525b' }}
      >
        {prefix ?? label}
      </span>
      <div className="flex items-center gap-1">
        <SignalDot active={signals.mom} label="MOM" variant={variant} outlined={outlined} />
        <SignalDot active={signals.vol} label="VOL" variant={variant} outlined={outlined} />
        <SignalDot active={signals.rsi} label="RSI" variant={variant} outlined={outlined} />
        <SignalDot active={signals.bb} label="BB" variant={variant} outlined={outlined} />
      </div>
      <span
        className={cn('text-[8px] font-mono ml-auto', countColor)}
        style={
          signals.count >= 3 ? { color } :
          signals.count >= 1 ? { color: '#ffd600' } :
          undefined
        }
      >
        {signals.count}/4
      </span>
    </div>
  );
}

function AssetCardComponent({ card }: { card: AssetCard }) {
  const rsiColor = card.rsi != null
    ? card.rsi < 30 ? 'text-[#00c853]'
    : card.rsi > 70 ? 'text-[#ff1744]'
    : 'text-amber-400'
    : 'text-zinc-600';

  // Arrow follows the 1h price change (not the 15m trend direction)
  const h1 = card.priceChange1h;
  const trendArrow = h1 != null && h1 > 0.05 ? '\u2191'
    : h1 != null && h1 < -0.05 ? '\u2193'
    : '\u2192';
  const trendColor = h1 != null && h1 > 0.05 ? 'text-[#00c853]'
    : h1 != null && h1 < -0.05 ? 'text-[#ff1744]'
    : 'text-zinc-400';

  const pos = card.activePosition;
  const inTrade = pos != null;
  const positionSide = pos?.position_type; // 'long' | 'short'

  // ── Fix 1: Determine row ordering ─────────────────────────────────
  // In trade: show trade direction first. Not in trade: stronger direction first.
  const bearFirst = inTrade
    ? positionSide === 'short'
    : card.bearSignals.count > card.bullSignals.count;

  // ── Fix 2: Determine entry vs live signals ────────────────────────
  const hasEntrySignals = inTrade && card.entrySignals != null;
  const entryDir = card.entryDirection;

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
      <div className="flex flex-wrap gap-2 mb-2">
        <PriceChange label="24h" value={card.priceChange24h} />
        <PriceChange label="1h" value={card.priceChange1h} />
      </div>

      {/* ── Signal Display (Fixes 1, 2, 3) ─────────────────────────── */}
      <div className="space-y-1 mb-2">
        {inTrade && hasEntrySignals ? (
          /* Fix 2: In trade — show entry signals + current live state */
          <>
            <SignalRow
              label={entryDir === 'short' ? 'Bear' : 'Bull'}
              signals={card.entrySignals!}
              variant={entryDir === 'short' ? 'bear' : 'bull'}
              prefix="Entry"
            />
            <SignalRow
              label={entryDir === 'short' ? 'Bear' : 'Bull'}
              signals={entryDir === 'short' ? card.bearSignals : card.bullSignals}
              variant={entryDir === 'short' ? 'bear' : 'bull'}
              outlined
              prefix="Now"
            />
          </>
        ) : (
          /* Not in trade (or no entry data): show both directions, Fix 1 ordering */
          <>
            {bearFirst ? (
              <>
                <SignalRow label="Bear" signals={card.bearSignals} variant="bear" />
                <SignalRow label="Bull" signals={card.bullSignals} variant="bull" />
              </>
            ) : (
              <>
                <SignalRow label="Bull" signals={card.bullSignals} variant="bull" />
                <SignalRow label="Bear" signals={card.bearSignals} variant="bear" />
              </>
            )}
          </>
        )}
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
  const { strategyLog, openPositions, trades, signalStates } = useSupabase();

  const assetCards = useMemo(() => {
    const ACTIVE_ASSETS = new Set(['BTC', 'ETH', 'SOL', 'XRP']);

    // Index signal_state rows by pair for quick lookup
    const signalsByPair = new Map<string, SignalState[]>();
    for (const ss of signalStates) {
      const arr = signalsByPair.get(ss.pair) ?? [];
      arr.push(ss);
      signalsByPair.set(ss.pair, arr);
    }

    // Find open trades for entry signal parsing (Fix 2)
    const openTradeByAsset = new Map<string, { reason?: string; position_type: string }>();
    for (const t of trades) {
      if (t.status !== 'open') continue;
      const asset = extractBaseAsset(t.pair);
      if (!openTradeByAsset.has(asset)) {
        openTradeByAsset.set(asset, { reason: t.reason, position_type: t.position_type });
      }
    }

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

      // Fix 3: Build directional signals from signal_state (real-time, per-direction)
      const pairSignals = signalsByPair.get(log.pair) ?? [];
      let bullSignals: DirectionSignals;
      let bearSignals: DirectionSignals;

      if (pairSignals.length > 0) {
        // Primary: use signal_state table (updated every 5s by engine)
        bullSignals = buildDirectionSignals(pairSignals, 'bull');
        bearSignals = buildDirectionSignals(pairSignals, 'bear');
      } else {
        // Fallback: use strategy_log signal fields (already direction-filtered by engine)
        // These are for the "active side" only — map them to the correct direction
        const side = log.signal_side;
        const logSignals: DirectionSignals = {
          mom: log.signal_mom === true,
          vol: log.signal_vol === true,
          rsi: log.signal_rsi === true,
          bb: log.signal_bb === true,
          count: log.signal_count ?? 0,
        };
        const empty: DirectionSignals = { mom: false, vol: false, rsi: false, bb: false, count: 0 };

        if (side === 'short') {
          bullSignals = empty;
          bearSignals = logSignals;
        } else if (side === 'long') {
          bullSignals = logSignals;
          bearSignals = empty;
        } else {
          // No active side — use bull_count/bear_count to pick
          const bc = log.bull_count ?? 0;
          const brc = log.bear_count ?? 0;
          if (bc >= brc) {
            bullSignals = logSignals;
            bearSignals = empty;
          } else {
            bullSignals = empty;
            bearSignals = logSignals;
          }
        }
      }

      // Fix 2: Parse entry signals from open trade reason
      const openTrade = openTradeByAsset.get(asset);
      const entrySignals = openTrade ? parseEntrySignals(openTrade.reason) : null;
      const entryDirection = openTrade
        ? (openTrade.position_type === 'short' ? 'short' as const : 'long' as const)
        : null;

      cards.push({
        asset,
        currentPrice: price,
        priceChange24h: log.price_change_24h ?? null,
        priceChange1h: log.price_change_1h ?? null,
        priceChange15m: log.price_change_15m ?? null,
        direction: log.direction ?? null,
        rsi: log.rsi ?? null,
        volumeRatio: log.volume_ratio ?? null,
        bbPosition: bbPos,
        activePosition: activePos,
        lastTimestamp: log.timestamp,
        bullSignals,
        bearSignals,
        entrySignals,
        entryDirection,
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
  }, [strategyLog, openPositions, trades, signalStates]);

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
