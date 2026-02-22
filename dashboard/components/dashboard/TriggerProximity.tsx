'use client';

import { useMemo } from 'react';
import { useSupabase } from '@/components/providers/SupabaseProvider';
import { cn } from '@/lib/utils';
import type { StrategyLog, Exchange, OpenPosition } from '@/lib/types';

// ── Engine thresholds (Focused Signal v6.1) ─────────────────────────
// Used ONLY as fallback when DB doesn't have signal_count/signal_* fields
const RSI_LONG_THRESHOLD = 35;     // was 40 — matches scalp.py RSI_THRESHOLD_LONG
const RSI_SHORT_THRESHOLD = 65;    // was 60 — matches scalp.py RSI_THRESHOLD_SHORT
const MOMENTUM_MIN_PCT = 0.20;     // matches scalp.py MOMENTUM_MIN_PCT (v3.14.3)
const VOL_SPIKE_RATIO = 0.8;       // was 1.2 — matches scalp.py VOLUME_SPIKE_RATIO
const BB_TREND_UPPER = 0.85;
const BB_TREND_LOWER = 0.15;

interface IndicatorStatus {
  active: boolean;
  label: string;
  direction?: 'bull' | 'bear' | null;
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
  // 15m trend direction (from engine, info only)
  trend: 'bullish' | 'bearish' | 'neutral';
  // Signal state from bot (or fallback calc)
  signalSide: 'long' | 'short' | null;
  indicators: IndicatorStatus[];   // 4 indicators for the active side (legacy)
  bullIndicators: IndicatorStatus[];  // 4 bull indicators (MOM, VOL, RSI, BB)
  bearIndicators: IndicatorStatus[];  // 4 bear indicators (MOM, VOL, RSI, BB)
  signalCount: number;
  bullCount: number;               // directional bull signal count
  bearCount: number;               // directional bear signal count
  // Overall
  overallStatus: string;
  statusColor: string;
  // Skip reason from bot (why it's not entering)
  skipReason: string | null;
  // Active position on this pair (if any)
  activePosition: OpenPosition | null;
}

/** Derive 15m trend direction — use DB field if present, else compute from DI/ADX. */
function deriveTrend(log: StrategyLog): 'bullish' | 'bearish' | 'neutral' {
  if (log.direction === 'bullish' || log.direction === 'bearish' || log.direction === 'neutral') {
    return log.direction;
  }
  const adx = log.adx ?? 0;
  const plusDi = log.plus_di ?? 0;
  const minusDi = log.minus_di ?? 0;
  if (adx > 25 && Math.abs(plusDi - minusDi) > 5) {
    return plusDi > minusDi ? 'bullish' : 'bearish';
  }
  if (adx >= 20) {
    return plusDi > minusDi ? 'bullish' : 'bearish';
  }
  return 'neutral';
}

/**
 * Build trigger info from strategy_log entry.
 * Primary: reads signal_count/signal_* fields written by the bot (exact match).
 * Fallback: recalculates from indicator values if fields are missing (old log entries).
 */
function computeTrigger(log: StrategyLog): TriggerInfo {
  const pair = log.pair;
  const exchange: Exchange = log.exchange ?? 'delta';
  const isFutures = exchange === 'delta';

  const rsi = log.rsi ?? null;
  const volumeRatio = log.volume_ratio ?? null;
  const priceChangePct = log.price_change_15m ?? null;
  const currentPrice = log.current_price ?? null;
  const bbUpper = log.bb_upper ?? null;
  const bbLower = log.bb_lower ?? null;
  const hasData = rsi != null;
  const trend = deriveTrend(log);

  // ── PRIMARY: use bot-written signal state if available ──────────────
  const hasBotSignals = log.signal_count != null;

  if (hasBotSignals) {
    const signalCount = log.signal_count ?? 0;
    const signalSide = (log.signal_side === 'long' || log.signal_side === 'short')
      ? log.signal_side : null;
    const bullCount = log.bull_count ?? 0;
    const bearCount = log.bear_count ?? 0;

    // Determine signal direction for dot coloring
    const dotDir = signalSide === 'short' ? 'bear' as const
      : signalSide === 'long' ? 'bull' as const
      : (bearCount > bullCount ? 'bear' as const : 'bull' as const);

    const indicators: IndicatorStatus[] = [
      { active: log.signal_mom === true, label: 'MOM', direction: dotDir },
      { active: log.signal_vol === true, label: 'VOL', direction: dotDir },
      { active: log.signal_rsi === true, label: 'RSI', direction: dotDir },
      { active: log.signal_bb === true,  label: 'BB',  direction: dotDir },
    ];

    // Per-direction indicators from bot breakdown fields
    const bullIndicators: IndicatorStatus[] = [
      { active: log.bull_mom === true, label: 'MOM', direction: 'bull' },
      { active: log.bull_vol === true, label: 'VOL', direction: 'bull' },
      { active: log.bull_rsi === true, label: 'RSI', direction: 'bull' },
      { active: log.bull_bb === true,  label: 'BB',  direction: 'bull' },
    ];
    const bearIndicators: IndicatorStatus[] = [
      { active: log.bear_mom === true, label: 'MOM', direction: 'bear' },
      { active: log.bear_vol === true, label: 'VOL', direction: 'bear' },
      { active: log.bear_rsi === true, label: 'RSI', direction: 'bear' },
      { active: log.bear_bb === true,  label: 'BB',  direction: 'bear' },
    ];

    let overallStatus: string;
    let statusColor: string;

    if (!hasData) {
      overallStatus = 'Awaiting data...';
      statusColor = 'text-zinc-600';
    } else if (signalCount >= 3) {
      overallStatus = `${signalCount}/4 — TRADE READY`;
      statusColor = 'text-[#00c853]';
    } else if (signalCount >= 1) {
      overallStatus = `${signalCount}/4 — Needs ${3 - signalCount} more`;
      statusColor = 'text-[#ffd600]';
    } else {
      overallStatus = '0/4 — Scanning';
      statusColor = 'text-zinc-500';
    }

    // Skip reason from bot
    const skipReason = log.skip_reason || null;

    return {
      pair, exchange, isFutures, hasData,
      rsi, volumeRatio, priceChangePct, currentPrice, bbUpper, bbLower,
      trend, signalSide, indicators, bullIndicators, bearIndicators,
      signalCount, bullCount, bearCount,
      overallStatus, statusColor,
      skipReason,
      activePosition: null,
    };
  }

  // ── FALLBACK: recalculate from indicator values (old log entries) ────
  const longIndicators: IndicatorStatus[] = [];
  let longCount = 0;

  const momBull = priceChangePct != null && priceChangePct >= MOMENTUM_MIN_PCT;
  longIndicators.push({ active: momBull, label: 'MOM', direction: 'bull' });
  if (momBull) longCount++;

  const volHigh = volumeRatio != null && volumeRatio >= VOL_SPIKE_RATIO;
  const volBull = volHigh && (priceChangePct == null || priceChangePct >= 0);
  longIndicators.push({ active: volBull, label: 'VOL', direction: 'bull' });
  if (volBull) longCount++;

  const rsiLong = rsi != null && rsi < RSI_LONG_THRESHOLD;
  longIndicators.push({ active: rsiLong, label: 'RSI', direction: 'bull' });
  if (rsiLong) longCount++;

  const bbRange = (bbUpper ?? 0) - (bbLower ?? 0);
  const bbPos = bbRange > 0 && currentPrice != null && bbLower != null
    ? (currentPrice - bbLower) / bbRange : 0.5;
  const bbLongActive = bbPos <= BB_TREND_LOWER;
  longIndicators.push({ active: bbLongActive, label: 'BB', direction: 'bull' });
  if (bbLongActive) longCount++;

  const shortIndicators: IndicatorStatus[] = [];
  let shortCount = 0;

  const momBear = priceChangePct != null && priceChangePct <= -MOMENTUM_MIN_PCT;
  shortIndicators.push({ active: momBear, label: 'MOM', direction: 'bear' });
  if (momBear) shortCount++;

  const volBear = volHigh && (priceChangePct == null || priceChangePct <= 0);
  shortIndicators.push({ active: volBear, label: 'VOL', direction: 'bear' });
  if (volBear) shortCount++;

  const rsiShort = rsi != null && rsi > RSI_SHORT_THRESHOLD;
  shortIndicators.push({ active: rsiShort, label: 'RSI', direction: 'bear' });
  if (rsiShort) shortCount++;

  const bbShortActive = isFutures && bbPos >= BB_TREND_UPPER;
  shortIndicators.push({ active: bbShortActive, label: 'BB', direction: 'bear' });
  if (bbShortActive) shortCount++;

  // Pick the stronger side
  const bestLong = longCount;
  const bestShort = isFutures ? shortCount : 0;
  const signalCount = Math.max(bestLong, bestShort);
  const bullCount = bestLong;
  const bearCount = bestShort;
  const signalSide = bestLong >= bestShort
    ? (bestLong > 0 ? 'long' as const : null)
    : (bestShort > 0 ? 'short' as const : null);
  const indicators = bestLong >= bestShort ? longIndicators : shortIndicators;

  let overallStatus: string;
  let statusColor: string;

  if (!hasData) {
    overallStatus = 'Awaiting data...';
    statusColor = 'text-zinc-600';
  } else if (signalCount >= 3) {
    overallStatus = `${signalCount}/4 — TRADE READY`;
    statusColor = 'text-[#00c853]';
  } else if (signalCount >= 1) {
    overallStatus = `${signalCount}/4 — Needs ${3 - signalCount} more`;
    statusColor = 'text-[#ffd600]';
  } else {
    overallStatus = '0/4 — Scanning';
    statusColor = 'text-zinc-500';
  }

  return {
    pair, exchange, isFutures, hasData,
    rsi, volumeRatio, priceChangePct, currentPrice, bbUpper, bbLower,
    trend, signalSide, indicators,
    bullIndicators: longIndicators,
    bearIndicators: shortIndicators,
    signalCount, bullCount, bearCount,
    overallStatus, statusColor,
    skipReason: null,
    activePosition: null,
  };
}

// ── Signal bar (fills based on signal count) ────────────────────────────
function SignalBar({ count, variant = 'bull' }: { count: number; variant?: 'bull' | 'bear' }) {
  const filled = (count / 4) * 100;
  const readyColor = variant === 'bear' ? '#ff1744' : '#00c853';
  const color =
    count >= 3 ? readyColor :
    count >= 1 ? '#ffd600' :
    '#71717a';

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
function Dot({ active, label, direction }: { active: boolean; label: string; direction?: 'bull' | 'bear' | null }) {
  const isBear = direction === 'bear';
  const activeColor = isBear ? 'bg-[#ff1744] border-[#ff1744] shadow-[0_0_6px_rgba(255,23,68,0.5)]'
    : 'bg-[#00c853] border-[#00c853] shadow-[0_0_6px_rgba(0,200,83,0.5)]';
  const activeText = isBear ? 'text-[#ff1744]' : 'text-[#00c853]';

  return (
    <div className="flex flex-col items-center gap-0.5">
      <div
        className={cn(
          'w-3 h-3 md:w-2.5 md:h-2.5 rounded-full border transition-all duration-500',
          active ? activeColor : 'bg-zinc-800 border-zinc-700',
        )}
      />
      <span className={cn(
        'text-[8px] font-mono leading-none',
        active ? activeText : 'text-zinc-600',
      )}>
        {label}
      </span>
    </div>
  );
}

// ── Trend badge ─────────────────────────────────────────────────────
function TrendBadge({ trend }: { trend: 'bullish' | 'bearish' | 'neutral' }) {
  const cfg = {
    bullish:  { icon: '\u2191', label: '15m Bull', color: 'text-[#00c853]', bg: 'bg-[#00c853]/10' },
    bearish:  { icon: '\u2193', label: '15m Bear', color: 'text-[#ff1744]', bg: 'bg-[#ff1744]/10' },
    neutral:  { icon: '\u2192', label: '15m Flat', color: 'text-zinc-400',  bg: 'bg-zinc-700/30' },
  }[trend];

  return (
    <span className={cn(
      'inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-mono font-medium',
      cfg.color, cfg.bg,
    )}>
      {cfg.icon} {cfg.label}
    </span>
  );
}

function extractBaseAsset(pair: string): string {
  if (pair.includes('/')) return pair.split('/')[0];
  return pair.replace(/USD.*$/, '');
}

// Only show our 4 active trading pairs (filter out stale BNB/DOGE/etc.)
const ACTIVE_ASSETS = new Set(['BTC', 'ETH', 'SOL', 'XRP']);

export function TriggerProximity() {
  const { strategyLog, openPositions } = useSupabase();

  const triggers = useMemo(() => {
    const latestByPair = new Map<string, StrategyLog>();
    for (const log of strategyLog) {
      if (log.pair) {
        const asset = extractBaseAsset(log.pair);
        if (!ACTIVE_ASSETS.has(asset)) continue;
        // Only show Delta futures pairs (user requested: remove Binance)
        const exchange = log.exchange ?? 'delta';
        if (exchange !== 'delta') continue;
        const key = `${log.pair}-${exchange}`;
        if (!latestByPair.has(key)) {
          latestByPair.set(key, log);
        }
      }
    }

    // Build lookup of open SCALP positions by base asset + exchange
    // Only show "IN TRADE" badge for scalp positions (not options)
    const OPTION_SYMBOL_RE = /\d{6}-\d+-[CP]/;
    const positionMap = new Map<string, OpenPosition>();
    for (const pos of (openPositions ?? [])) {
      // Skip options positions — they show in Options Overview, not here
      if (pos.strategy === 'options_scalp' || OPTION_SYMBOL_RE.test(pos.pair)) continue;
      const asset = extractBaseAsset(pos.pair);
      const key = `${asset}-${pos.exchange}`;
      positionMap.set(key, pos);
    }

    const results: TriggerInfo[] = [];
    for (const log of Array.from(latestByPair.values())) {
      const trigger = computeTrigger(log);
      const asset = extractBaseAsset(trigger.pair);
      const posKey = `${asset}-${trigger.exchange}`;
      trigger.activePosition = positionMap.get(posKey) ?? null;
      results.push(trigger);
    }

    // Priority: 1) in-trade pairs first, 2) highest signal count, 3) has data
    results.sort((a, b) => {
      const aInTrade = a.activePosition != null ? 1 : 0;
      const bInTrade = b.activePosition != null ? 1 : 0;
      if (aInTrade !== bInTrade) return bInTrade - aInTrade;
      if (a.signalCount !== b.signalCount) return b.signalCount - a.signalCount;
      if (a.hasData && !b.hasData) return -1;
      if (!a.hasData && b.hasData) return 1;
      return 0;
    });

    return results;
  }, [strategyLog, openPositions]);

  return (
    <div className="bg-[#0d1117] border border-zinc-800 rounded-xl p-3 md:p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider">
          Entry Signals — Futures
        </h3>
        <span className="text-[9px] text-zinc-600 font-mono">need 3/4</span>
      </div>

      {triggers.length === 0 ? (
        <p className="text-sm text-zinc-500 text-center py-8">No pairs tracked yet</p>
      ) : (
        <div className="space-y-3 max-h-none md:max-h-[600px] overflow-y-auto overflow-x-hidden pr-1">
          {triggers.map((t) => {
            const hasActivePos = t.activePosition != null;
            const posPnl = t.activePosition?.current_pnl ?? null;
            const peakPnl = t.activePosition?.peak_pnl ?? 0;
            // Only trust trailing if peak P&L confirms it (match LivePositions logic)
            const isTrailing = (
              t.activePosition?.position_state === 'trailing'
              && peakPnl >= 0.30
            );
            const isNegative = posPnl != null && posPnl < 0;
            const posLabel = isTrailing ? 'TRAILING'
              : isNegative ? 'AT RISK'
              : 'HOLDING';
            const posColor = isTrailing ? 'bg-[#00c853]/10 text-[#00c853]'
              : isNegative ? 'bg-[#ff1744]/10 text-[#ff1744]'
              : 'bg-amber-400/10 text-amber-400';
            const borderClr = isTrailing ? 'border-[#00c853]/30'
              : isNegative ? 'border-[#ff1744]/20'
              : 'border-amber-400/30';

            return (
            <div
              key={`${t.pair}-${t.exchange}`}
              className={cn(
                'bg-zinc-900/40 border rounded-lg p-3',
                hasActivePos ? borderClr : 'border-zinc-800/50',
              )}
            >
              {/* Header */}
              <div className="flex flex-wrap items-center justify-between gap-1 mb-3">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-white">{extractBaseAsset(t.pair)}</span>
                  {t.currentPrice != null && (
                    <span className="text-[10px] font-mono text-zinc-500">
                      ${t.currentPrice.toLocaleString()}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {t.isFutures && t.hasData && <TrendBadge trend={t.trend} />}
                  {hasActivePos ? (
                    <span className={cn(
                      'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium',
                      posColor,
                    )}>
                      {isTrailing && <span className="w-1.5 h-1.5 rounded-full bg-[#00c853] animate-pulse" />}
                      IN TRADE — {posLabel}
                      {posPnl != null && ` ${posPnl >= 0 ? '+' : ''}${posPnl.toFixed(2)}%`}
                    </span>
                  ) : (
                    <div className="flex flex-col items-end gap-0.5">
                      <span className={cn('text-[11px] font-medium', t.statusColor)}>
                        {t.overallStatus}
                      </span>
                      {t.skipReason && (
                        <span className="text-[9px] font-mono text-amber-400/70 truncate max-w-[180px]">
                          ⚠ {t.skipReason.replace(/_/g, ' ')}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {t.hasData ? (
                <div className={cn('space-y-2.5', hasActivePos && 'opacity-40')}>
                  {/* Dual signal rows: bull + bear, with inline dots */}
                  <div className="space-y-2">
                    {/* Bull row: bar + dots */}
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className={cn(
                          'text-[10px] font-mono w-8 shrink-0',
                          t.signalSide === 'long' ? 'text-[#00c853] font-bold' : 'text-zinc-600',
                        )}>
                          Bull
                        </span>
                        <SignalBar count={t.bullCount} />
                        <span className={cn(
                          'text-[10px] font-mono w-8 text-right',
                          t.bullCount >= 3 ? 'text-[#00c853]' : t.bullCount >= 1 ? 'text-[#ffd600]' : 'text-zinc-600',
                        )}>
                          {t.bullCount}/4
                        </span>
                      </div>
                      <div className="flex items-center gap-3 ml-8">
                        {t.bullIndicators.map((ind, i) => (
                          <Dot key={`bull-${i}`} active={ind.active} label={ind.label} direction="bull" />
                        ))}
                      </div>
                    </div>
                    {/* Bear row: bar + dots (futures only) */}
                    {t.isFutures && (
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span className={cn(
                            'text-[10px] font-mono w-8 shrink-0',
                            t.signalSide === 'short' ? 'text-[#ff1744] font-bold' : 'text-zinc-600',
                          )}>
                            Bear
                          </span>
                          <SignalBar count={t.bearCount} variant="bear" />
                          <span className={cn(
                            'text-[10px] font-mono w-8 text-right',
                            t.bearCount >= 3 ? 'text-[#ff1744]' : t.bearCount >= 1 ? 'text-[#ffd600]' : 'text-zinc-600',
                          )}>
                            {t.bearCount}/4
                          </span>
                        </div>
                        <div className="flex items-center gap-3 ml-8">
                          {t.bearIndicators.map((ind, i) => (
                            <Dot key={`bear-${i}`} active={ind.active} label={ind.label} direction="bear" />
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
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
          );
          })}
        </div>
      )}
    </div>
  );
}
