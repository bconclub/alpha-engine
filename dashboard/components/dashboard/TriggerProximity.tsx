'use client';

import { useMemo } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { cn } from '@/lib/utils';
import type { StrategyLog, Exchange } from '@/lib/types';

interface TriggerInfo {
  pair: string;
  exchange: Exchange;
  rsi: number | null;
  longDistancePct: number;
  shortDistancePct: number;
  longReady: boolean;
  shortReady: boolean;
  isFutures: boolean;
  macdStatus: string;
  overallStatus: string;
  statusColor: string;
  triggerText: string;
  hasIndicatorData: boolean;
}

// Thresholds matching engine: momentum buy < 35, short > 65
const RSI_BUY_THRESHOLD = 35;
const RSI_SHORT_THRESHOLD = 65;

function computeTrigger(log: StrategyLog): TriggerInfo {
  const pair = log.pair;
  const exchange: Exchange = log.exchange ?? 'binance';
  const rsi = log.rsi ?? null;
  const isFutures = exchange === 'delta';

  let longDistancePct = 100;
  let shortDistancePct = 100;
  let longReady = false;
  let shortReady = false;

  if (rsi != null) {
    // Buy distance: how far RSI is from the buy threshold
    if (rsi > RSI_BUY_THRESHOLD) {
      longDistancePct = ((rsi - RSI_BUY_THRESHOLD) / RSI_BUY_THRESHOLD) * 100;
    } else {
      longDistancePct = 0;
      longReady = true;
    }

    // Short distance: how far RSI is from the short threshold
    if (rsi < RSI_SHORT_THRESHOLD) {
      shortDistancePct = ((RSI_SHORT_THRESHOLD - rsi) / (100 - RSI_SHORT_THRESHOLD)) * 100;
    } else {
      shortDistancePct = 0;
      shortReady = true;
    }
  }

  // MACD status
  let macdStatus = 'No data';
  if (log.macd_histogram != null) {
    const hist = log.macd_histogram;
    if (Math.abs(hist) < 0.0005) {
      macdStatus = 'Converging — possible cross soon';
    } else if (hist > 0) {
      macdStatus = 'Bullish — above signal';
    } else {
      macdStatus = 'Bearish — below signal';
    }
  }

  // Build trigger text: "RSI=72 → need <35 for long (far) | need >65 for short (READY ⚡)"
  let triggerText = 'Awaiting indicator data...';
  let overallStatus = 'Watching';
  let statusColor = 'text-zinc-400';

  if (rsi != null) {
    const longLabel = longReady
      ? 'READY ⚡'
      : longDistancePct < 25 ? 'close' : 'far';
    const shortLabel = shortReady
      ? 'READY ⚡'
      : shortDistancePct < 25 ? 'close' : 'far';

    const parts: string[] = [];
    parts.push(`need <${RSI_BUY_THRESHOLD} for long (${longLabel})`);
    if (isFutures) {
      parts.push(`need >${RSI_SHORT_THRESHOLD} for short (${shortLabel})`);
    }
    triggerText = `RSI=${rsi.toFixed(0)} → ${parts.join(' | ')}`;

    // Determine closest signal
    const closestDist = isFutures
      ? Math.min(longDistancePct, shortDistancePct)
      : longDistancePct;

    if (longReady || shortReady) {
      overallStatus = 'Signal Ready ⚡';
      statusColor = 'text-[#00c853]';
    } else if (closestDist < 25) {
      overallStatus = 'Getting Close';
      statusColor = 'text-[#ffd600]';
    } else {
      overallStatus = 'Watching';
      statusColor = 'text-zinc-500';
    }
  }

  return {
    pair,
    exchange,
    rsi,
    longDistancePct,
    shortDistancePct,
    longReady,
    shortReady,
    isFutures,
    macdStatus,
    overallStatus,
    statusColor,
    triggerText,
    hasIndicatorData: rsi != null,
  };
}

function ProximityBar({ distance, label, ready }: { distance: number; label: string; ready: boolean }) {
  const filled = ready ? 100 : Math.max(0, Math.min(100, 100 - distance));
  const color = ready
    ? '#00c853'
    : distance < 25
      ? '#ffd600'
      : '#ff1744';

  return (
    <div className="flex items-center gap-2 mt-1">
      <span className="text-[10px] text-zinc-500 w-10 shrink-0">{label}</span>
      <div className="flex-1 h-2 bg-zinc-800 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${filled}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-[10px] font-mono text-zinc-500 w-16 text-right">
        {ready ? 'READY ⚡' : `${distance.toFixed(0)}% away`}
      </span>
    </div>
  );
}

export function TriggerProximity() {
  const { strategyLog } = useSupabase();

  const triggers = useMemo(() => {
    // Get latest log per pair (strategyLog is ordered by created_at DESC)
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

    // Sort: closest to any trigger first
    results.sort((a, b) => {
      if (a.hasIndicatorData && !b.hasIndicatorData) return -1;
      if (!a.hasIndicatorData && b.hasIndicatorData) return 1;
      const aMin = a.isFutures
        ? Math.min(a.longDistancePct, a.shortDistancePct)
        : a.longDistancePct;
      const bMin = b.isFutures
        ? Math.min(b.longDistancePct, b.shortDistancePct)
        : b.longDistancePct;
      return aMin - bMin;
    });

    return results;
  }, [strategyLog]);

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-5">
      <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-4">
        What Could Trigger Next
      </h3>

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
              <div className="flex items-center justify-between mb-2">
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
                </div>
                <span className={cn('text-xs font-medium', t.statusColor)}>
                  {t.overallStatus}
                </span>
              </div>

              {/* RSI proximity bars */}
              {t.rsi != null ? (
                <div className="mb-2 space-y-1">
                  <ProximityBar
                    distance={t.longDistancePct}
                    label="Long"
                    ready={t.longReady}
                  />
                  {t.isFutures && (
                    <ProximityBar
                      distance={t.shortDistancePct}
                      label="Short"
                      ready={t.shortReady}
                    />
                  )}
                </div>
              ) : (
                <p className="text-[11px] text-zinc-600 mb-2">Awaiting indicator data from bot...</p>
              )}

              {/* MACD */}
              <div className="text-[11px] text-zinc-500 mb-1">
                MACD: <span className="text-zinc-400">{t.macdStatus}</span>
              </div>

              {/* Trigger text */}
              <p className="text-[10px] text-zinc-600 italic">{t.triggerText}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
