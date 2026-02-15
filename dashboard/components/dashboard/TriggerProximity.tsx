'use client';

import { useMemo } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { cn } from '@/lib/utils';
import type { StrategyLog, Exchange } from '@/lib/types';

// ── Engine thresholds (Trend Sniper v4.0) ───────────────────────────
const RSI_LONG_THRESHOLD = 30;
const RSI_SHORT_THRESHOLD = 70;
const MOMENTUM_MIN_PCT = 0.30;
const VOL_SPIKE_RATIO = 2.0;

interface IndicatorStatus {
  active: boolean;
  label: string;
}

interface TriggerInfo {
  pair: string;
  exchange: Exchange;
  isFutures: boolean;
  hasData: boolean;
  rsi: number | null;
  volumeRatio: number | null;
  priceChangePct: number | null;
  currentPrice: number | null;
  bbUpper: number | null;
  bbLower: number | null;
  // 4 indicators per side
  longIndicators: IndicatorStatus[];
  longCount: number;
  shortIndicators: IndicatorStatus[];
  shortCount: number;
  // Overall
  bestCount: number;
  overallStatus: string;
  statusColor: string;
}

function computeTrigger(log: StrategyLog): TriggerInfo {
  const pair = log.pair;
  const exchange: Exchange = log.exchange ?? 'binance';
  const isFutures = exchange === 'delta';

  const rsi = log.rsi ?? null;
  const volumeRatio = log.volume_ratio ?? null;
  const priceChangePct = log.price_change_15m ?? null;
  const currentPrice = log.current_price ?? null;
  const bbUpper = log.bb_upper ?? null;
  const bbLower = log.bb_lower ?? null;
  const hasData = rsi != null;

  // ── Build 4 indicators for LONG ────────────────────────────────────
  const longIndicators: IndicatorStatus[] = [];
  let longCount = 0;

  const momBull = priceChangePct != null && priceChangePct >= MOMENTUM_MIN_PCT;
  longIndicators.push({ active: momBull, label: 'MOM' });
  if (momBull) longCount++;

  const volHigh = volumeRatio != null && volumeRatio >= VOL_SPIKE_RATIO;
  const volBull = volHigh && (priceChangePct == null || priceChangePct >= 0);
  longIndicators.push({ active: volBull, label: 'VOL' });
  if (volBull) longCount++;

  const rsiLong = rsi != null && rsi < RSI_LONG_THRESHOLD;
  longIndicators.push({ active: rsiLong, label: 'RSI' });
  if (rsiLong) longCount++;

  const bbBreakLong = currentPrice != null && bbUpper != null && currentPrice > bbUpper;
  longIndicators.push({ active: bbBreakLong, label: 'BB' });
  if (bbBreakLong) longCount++;

  // ── Build 4 indicators for SHORT ───────────────────────────────────
  const shortIndicators: IndicatorStatus[] = [];
  let shortCount = 0;

  const momBear = priceChangePct != null && priceChangePct <= -MOMENTUM_MIN_PCT;
  shortIndicators.push({ active: momBear, label: 'MOM' });
  if (momBear) shortCount++;

  const volBear = volHigh && (priceChangePct == null || priceChangePct <= 0);
  shortIndicators.push({ active: volBear, label: 'VOL' });
  if (volBear) shortCount++;

  const rsiShort = rsi != null && rsi > RSI_SHORT_THRESHOLD;
  shortIndicators.push({ active: rsiShort, label: 'RSI' });
  if (rsiShort) shortCount++;

  const bbBreakShort = currentPrice != null && bbLower != null && currentPrice < bbLower;
  shortIndicators.push({ active: bbBreakShort, label: 'BB' });
  if (bbBreakShort) shortCount++;

  // ── Overall status ─────────────────────────────────────────────────
  const bestCount = isFutures ? Math.max(longCount, shortCount) : longCount;

  let overallStatus: string;
  let statusColor: string;

  if (!hasData) {
    overallStatus = 'Awaiting data...';
    statusColor = 'text-zinc-600';
  } else if (bestCount >= 2) {
    overallStatus = `${bestCount}/4 — TRADE READY`;
    statusColor = 'text-[#00c853]';
  } else if (bestCount === 1) {
    overallStatus = '1/4 — Needs 1 more';
    statusColor = 'text-[#ffd600]';
  } else {
    overallStatus = '0/4 — Scanning';
    statusColor = 'text-zinc-500';
  }

  return {
    pair, exchange, isFutures, hasData,
    rsi, volumeRatio, priceChangePct, currentPrice, bbUpper, bbLower,
    longIndicators, longCount,
    shortIndicators, shortCount,
    bestCount, overallStatus, statusColor,
  };
}

// ── Signal bar (fills based on signal count, not RSI) ─────────────────
function SignalBar({ count }: { count: number }) {
  const filled = (count / 4) * 100;
  const color =
    count >= 2 ? '#00c853' :   // green — trade ready
    count === 1 ? '#ffd600' :  // yellow — 1 signal
    '#71717a';                 // gray — nothing

  return (
    <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
      <div
        className="h-full rounded-full transition-all duration-700"
        style={{ width: `${filled}%`, backgroundColor: color }}
      />
    </div>
  );
}

// ── Single indicator dot ─────────────────────────────────────────────
function Dot({ active, label }: { active: boolean; label: string }) {
  return (
    <div className="flex flex-col items-center gap-0.5">
      <div
        className={cn(
          'w-2.5 h-2.5 rounded-full border transition-all duration-500',
          active
            ? 'bg-[#00c853] border-[#00c853] shadow-[0_0_6px_rgba(0,200,83,0.5)]'
            : 'bg-zinc-800 border-zinc-700',
        )}
      />
      <span className={cn(
        'text-[8px] font-mono leading-none',
        active ? 'text-[#00c853]' : 'text-zinc-600',
      )}>
        {label}
      </span>
    </div>
  );
}

// ── Side row: bar + dots underneath ──────────────────────────────────
function SideRow({
  side,
  indicators,
  count,
}: {
  side: 'Long' | 'Short';
  indicators: IndicatorStatus[];
  count: number;
}) {
  const countColor =
    count >= 2 ? 'text-[#00c853]' :
    count === 1 ? 'text-[#ffd600]' :
    'text-zinc-600';

  return (
    <div className="space-y-1">
      {/* Bar row — fills based on signal count */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-zinc-500 w-8 shrink-0">{side}</span>
        <SignalBar count={count} />
        <span className={cn('text-[10px] font-mono w-16 text-right', countColor)}>
          {count >= 2 ? `${count}/4 ✓` : `${count}/4`}
        </span>
      </div>
      {/* Dots row (aligned under the bar) */}
      <div className="flex items-center gap-2 ml-10">
        <div className="flex items-center gap-2">
          {indicators.map((ind, i) => (
            <Dot key={i} active={ind.active} label={ind.label} />
          ))}
        </div>
      </div>
    </div>
  );
}

export function TriggerProximity() {
  const { strategyLog } = useSupabase();

  const triggers = useMemo(() => {
    const latestByPair = new Map<string, StrategyLog>();
    for (const log of strategyLog) {
      if (log.pair) {
        const key = `${log.pair}-${log.exchange ?? 'binance'}`;
        if (!latestByPair.has(key)) {
          latestByPair.set(key, log);
        }
      }
    }

    const results: TriggerInfo[] = [];
    for (const log of Array.from(latestByPair.values())) {
      results.push(computeTrigger(log));
    }

    results.sort((a, b) => {
      if (a.hasData && !b.hasData) return -1;
      if (!a.hasData && b.hasData) return 1;
      return b.bestCount - a.bestCount;
    });

    return results;
  }, [strategyLog]);

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider">
          Entry Signals
        </h3>
        <span className="text-[9px] text-zinc-600 font-mono">need 2/4</span>
      </div>

      {triggers.length === 0 ? (
        <p className="text-sm text-zinc-500 text-center py-8">No pairs tracked yet</p>
      ) : (
        <div className="space-y-3 max-h-[600px] overflow-y-auto pr-1">
          {triggers.map((t) => (
            <div
              key={`${t.pair}-${t.exchange}`}
              className="bg-zinc-900/40 border border-zinc-800/50 rounded-lg p-3"
            >
              {/* Header */}
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-white">{t.pair}</span>
                  <span
                    className={cn(
                      'inline-flex items-center justify-center w-4 h-4 rounded text-[8px] font-bold',
                      t.exchange === 'binance'
                        ? 'bg-[#f0b90b]/10 text-[#f0b90b]'
                        : 'bg-[#00d2ff]/10 text-[#00d2ff]',
                    )}
                  >
                    {t.exchange === 'binance' ? 'B' : 'D'}
                  </span>
                  {t.currentPrice != null && (
                    <span className="text-[10px] font-mono text-zinc-500">
                      ${t.currentPrice.toLocaleString()}
                    </span>
                  )}
                </div>
                <span className={cn('text-[11px] font-medium', t.statusColor)}>
                  {t.overallStatus}
                </span>
              </div>

              {t.hasData ? (
                <div className="space-y-2.5">
                  {/* Long: bar + dots */}
                  <SideRow
                    side="Long"
                    indicators={t.longIndicators}
                    count={t.longCount}
                  />
                  {/* Short: bar + dots (futures only) */}
                  {t.isFutures && (
                    <SideRow
                      side="Short"
                      indicators={t.shortIndicators}
                      count={t.shortCount}
                    />
                  )}
                  {/* Compact values */}
                  <div className="flex gap-3 pt-1 border-t border-zinc-800/50">
                    {t.rsi != null && (
                      <span className={cn(
                        'text-[9px] font-mono',
                        t.rsi < RSI_LONG_THRESHOLD ? 'text-[#00c853]' :
                        t.rsi > RSI_SHORT_THRESHOLD ? 'text-[#ff1744]' :
                        'text-zinc-500',
                      )}>
                        RSI {t.rsi.toFixed(0)}
                      </span>
                    )}
                    {t.volumeRatio != null && (
                      <span className={cn(
                        'text-[9px] font-mono',
                        t.volumeRatio >= VOL_SPIKE_RATIO ? 'text-[#00c853]' : 'text-zinc-500',
                      )}>
                        Vol {t.volumeRatio.toFixed(1)}x
                      </span>
                    )}
                    {t.priceChangePct != null && (
                      <span className={cn(
                        'text-[9px] font-mono',
                        Math.abs(t.priceChangePct) >= MOMENTUM_MIN_PCT ? 'text-[#00c853]' : 'text-zinc-500',
                      )}>
                        Mom {t.priceChangePct >= 0 ? '+' : ''}{t.priceChangePct.toFixed(2)}%
                      </span>
                    )}
                    {t.bbUpper != null && t.bbLower != null && t.currentPrice != null && (
                      <span className={cn(
                        'text-[9px] font-mono',
                        t.currentPrice > t.bbUpper || t.currentPrice < t.bbLower
                          ? 'text-[#00c853]' : 'text-zinc-500',
                      )}>
                        BB {t.currentPrice > t.bbUpper ? 'Above' : t.currentPrice < t.bbLower ? 'Below' : 'In'}
                      </span>
                    )}
                  </div>
                </div>
              ) : (
                <p className="text-[11px] text-zinc-600">Awaiting indicator data from bot...</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
